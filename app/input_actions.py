"""HTTP API actions for humanized mouse, keyboard and wheel input.

OS-level input blocks for as long as a human would take, so it runs in a
worker thread and the event loop keeps serving health checks, screenshots
and MCP while it plays. Actions are still serialized by the request lock,
so only one thread drives the X server at a time.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
from collections.abc import Awaitable, Callable
from typing import Any

import human_keyboard
import human_scroll
from human_keyboard import TypingProfile
from human_mouse import MouseProfile
from logger import get_logger
from playwright.async_api import Error as PlaywrightError
from system import InputError, System

log = get_logger(__name__)


class InputActionError(Exception):
    """An input action was rejected or could not complete."""


ActionHandler = Callable[[dict, Any, System], Awaitable[dict]]

_KEY_HOME = "home"
_WHEEL_DOWN = -1
_WHEEL_UP = 1

_SCROLL_AMOUNT_DEFAULT = -3
_SCROLL_MIN_CLICKS_DEFAULT = 2
_SCROLL_MAX_CLICKS_DEFAULT = 6
_SCROLL_CLICKS_LIMIT = 50
_SCROLL_DELAY_DEFAULT_S = 0.5
_SCROLL_MAX_GESTURES_DEFAULT = 500
_SCROLL_MAX_GESTURES_LIMIT = 10_000
_SCROLL_STALLS_TO_STOP = 2
_SCROLL_DRIFT_P = 0.4

# Accepted ranges for per-request tuning overrides.
_SPEED_BOUNDS = (0.1, 10.0)
_CURVATURE_BOUNDS = (0.0, 5.0)
_TREMOR_BOUNDS_PX = (0.0, 5.0)
_CLICK_HOLD_BOUNDS_S = (0.01, 5.0)
_CLICK_DELAY_BOUNDS_S = (0.0, 10.0)
_KEY_HOLD_BOUNDS_S = (0.01, 2.0)
_ROLLOVER_BOUNDS = (0.0, 1.0)
_TYPO_RATE_BOUNDS = (0.0, 0.2)
_NOTCH_GAP_BOUNDS_S = (0.015, 5.0)
_PROBABILITY_BOUNDS = (0.0, 1.0)
_SCROLL_TOP_TIMEOUT_S = 3.0
_SCROLL_TOP_POLL_S = 0.1
_SCROLL_TOP_FLICK_NOTCHES = (8, 14)
_SCROLL_TOP_MAX_GESTURES = 200
# Where to put a pointer that is outside the page before wheel scrolling.
_POINTER_HOME_FRAC = (0.3, 0.7)
_VIEWPORT_MARGIN_PX = 10

# Fields where an intermediate typo could be validated, masked, truncated
# or submitted. Typos are switched off for these.
_TYPO_SAFE_INPUT_TYPES = frozenset({"", "text", "search"})
_TYPO_UNSAFE_INPUT_MODES = frozenset({"numeric", "decimal", "tel", "email", "url"})
_TYPO_UNSAFE_AUTOCOMPLETE = (
    "one-time-code",
    "cc-",
    "password",
    "email",
    "tel",
    "url",
)
_TAG_INPUT = "input"
_TAG_TEXTAREA = "textarea"

_TYPO_REASON_NO_FIELD = "no_focused_text_field"
_TYPO_REASON_FIELD_TYPE = "field_type"
_TYPO_REASON_CONTENTEDITABLE = "contenteditable"
_TYPO_REASON_MAXLENGTH = "maxlength"
_TYPO_REASON_PATTERN = "pattern"
_TYPO_REASON_INPUTMODE = "inputmode"
_TYPO_REASON_AUTOCOMPLETE = "autocomplete"
_TYPO_REASON_CONTROL_CHARS = "control_characters"

_FIELD_STATE_JS = """el => {
    if (!el || el === document.body || el === document.documentElement) {
        return null;
    }
    const tag = el.tagName.toLowerCase();
    const hasValue = (tag === "input" || tag === "textarea")
        && typeof el.value === "string";
    let selStart = null;
    let selEnd = null;
    try {
        selStart = el.selectionStart;
        selEnd = el.selectionEnd;
    } catch (e) {
        selStart = null;
    }
    return {
        tag,
        type: tag === "input" ? (el.type || "").toLowerCase() : "",
        editable: !!el.isContentEditable,
        maxLength: typeof el.maxLength === "number" ? el.maxLength : -1,
        hasPattern: el.hasAttribute("pattern"),
        inputMode: (el.getAttribute("inputmode") || "").toLowerCase(),
        autocomplete: (el.getAttribute("autocomplete") || "").toLowerCase(),
        value: hasValue ? el.value : null,
        selStart,
        selEnd,
    };
}"""
_FIELD_VALUE_JS = "el => (typeof el.value === 'string' ? el.value : null)"
_VIEWPORT_JS = "() => ({w: window.innerWidth, h: window.innerHeight})"
# mozInnerScreenX/Y report where the viewport really sits on screen.
# Camoufox spoofs outerHeight/innerHeight, so those cannot be used.
_WINDOW_OFFSET_JS = """() => ({
    x: Math.round(window.mozInnerScreenX),
    y: Math.round(window.mozInnerScreenY)
})"""
_SCROLL_Y_JS = "() => window.scrollY"
_EDITABLE_FOCUSED_JS = """() => {
    const el = document.activeElement;
    if (!el) return false;
    const tag = el.tagName.toLowerCase();
    return tag === "input" || tag === "textarea" || el.isContentEditable;
}"""


def _number(cmd: dict, name: str, required: bool = False) -> float | None:
    value = cmd.get(name)
    if value is None:
        if required:
            raise InputActionError(f"{name} is required")
        return None
    # Numeric strings were accepted before validation existed; keep them.
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError as e:
            raise InputActionError(f"{name} must be a number") from e
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputActionError(f"{name} must be a number")
    if not math.isfinite(value):
        raise InputActionError(f"{name} must be finite")
    return float(value)


def _integer(cmd: dict, name: str, default: int) -> int:
    value = _number(cmd, name)
    if value is None:
        return default
    if not value.is_integer():
        raise InputActionError(f"{name} must be an integer")
    return int(value)


def _positive(cmd: dict, name: str) -> float | None:
    value = _number(cmd, name)
    if value is not None and value <= 0:
        raise InputActionError(f"{name} must be greater than 0")
    return value


def _flag(cmd: dict, name: str, default: bool) -> bool:
    value = cmd.get(name, default)
    if not isinstance(value, bool):
        raise InputActionError(f"{name} must be true or false")
    return value


def _xy(cmd: dict, required: bool) -> tuple[float | None, float | None]:
    x = _number(cmd, "x", required)
    y = _number(cmd, "y", required)
    if (x is None) != (y is None):
        raise InputActionError("x and y must be given together")
    return x, y


def _point(x: float | int, y: float | int) -> dict:
    return {"x": x, "y": y}


def _bounded(cmd: dict, name: str, bounds: tuple[float, float]) -> float | None:
    """Optional number that must fall inside bounds (inclusive)."""
    value = _number(cmd, name)
    lo, hi = bounds
    if value is not None and not lo <= value <= hi:
        raise InputActionError(f"{name} must be between {lo} and {hi}")
    return value


def _target(cmd: dict) -> tuple[float | None, float | None, float | None]:
    """Optional duration, width and height shared by pointer actions."""
    return _positive(cmd, "duration"), _positive(cmd, "w"), _positive(cmd, "h")


def _mouse_profile(cmd: dict, system: System) -> MouseProfile | None:
    """The session's mouse traits with any per-request overrides applied."""
    speed = _bounded(cmd, "speed", _SPEED_BOUNDS)
    curvature = _bounded(cmd, "curvature", _CURVATURE_BOUNDS)
    tremor = _bounded(cmd, "tremor", _TREMOR_BOUNDS_PX)
    click_hold = _bounded(cmd, "click_hold", _CLICK_HOLD_BOUNDS_S)
    click_delay = _bounded(cmd, "click_delay", _CLICK_DELAY_BOUNDS_S)
    overrides: dict[str, float] = {}
    if speed is not None:
        overrides["speed"] = system.mouse_profile.speed * speed
    if curvature is not None:
        overrides["curvature"] = curvature
    if tremor is not None:
        overrides["tremor_px"] = tremor
    if click_hold is not None:
        overrides["hold_median_s"] = click_hold
    if click_delay is not None:
        overrides["press_median_s"] = click_delay
    if not overrides:
        return None
    return dataclasses.replace(system.mouse_profile, **overrides)


async def read_window_offset(page: Any) -> dict | None:
    """Screen position of the page viewport, or None if it cannot be read."""
    try:
        result = await page.evaluate(_WINDOW_OFFSET_JS)
    except PlaywrightError as e:
        log.warning("window offset unreadable", extra={"error": str(e)})
        return None
    x = result.get("x") if isinstance(result, dict) else None
    y = result.get("y") if isinstance(result, dict) else None
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        log.warning("window offset not numeric", extra={"result": repr(result)})
        return None
    return {"x": int(x), "y": int(y)}


async def _refresh_window_offset(page: Any, system: System) -> None:
    """Re-measure where the page sits on screen before moving the pointer.

    Fullscreen, new tab windows and browser relaunches move the viewport,
    so a stored offset goes stale. The last good offset is kept when the
    page cannot be read.
    """
    offset = await read_window_offset(page)
    if offset is None:
        return
    if offset != system.window_offset:
        log.debug(
            "window offset changed",
            extra={"old": system.window_offset, "new": offset},
        )
    system.window_offset = offset


async def mouse_move(cmd: dict, page: Any, system: System) -> dict:
    x, y = _xy(cmd, required=True)
    duration, width, height = _target(cmd)
    profile = _mouse_profile(cmd, system)
    await _refresh_window_offset(page, system)
    landed = await asyncio.to_thread(
        system.move_mouse, x, y, duration, width, height, profile
    )
    return {"moved_to": _point(cmd["x"], cmd["y"]), "landed_at": _point(*landed)}


async def mouse_click(cmd: dict, page: Any, system: System) -> dict:
    x, y = _xy(cmd, required=False)
    duration, width, height = _target(cmd)
    profile = _mouse_profile(cmd, system)
    await _refresh_window_offset(page, system)
    clicked = await asyncio.to_thread(
        system.click, x, y, duration, width, height, profile
    )
    if x is None:
        return {"clicked_at": "current", "landed_at": _point(*clicked)}
    return {"clicked_at": _point(cmd["x"], cmd["y"]), "landed_at": _point(*clicked)}


async def system_click(cmd: dict, page: Any, system: System) -> dict:
    x, y = _xy(cmd, required=True)
    duration, width, height = _target(cmd)
    profile = _mouse_profile(cmd, system)
    await _refresh_window_offset(page, system)
    clicked = await asyncio.to_thread(
        system.click, x, y, duration, width, height, profile
    )
    return {"system_clicked": _point(cmd["x"], cmd["y"]), "landed_at": _point(*clicked)}


async def scroll(cmd: dict, page: Any, system: System) -> dict:
    amount = _integer(cmd, "amount", _SCROLL_AMOUNT_DEFAULT)
    if amount == 0:
        raise InputActionError("amount must be a non-zero integer")
    x, y = _xy(cmd, required=False)
    notch_gap = _bounded(cmd, "notch_gap", _NOTCH_GAP_BOUNDS_S)
    if x is not None:
        await _refresh_window_offset(page, system)
    await asyncio.to_thread(system.scroll, amount, x, y, notch_gap)
    return {"scrolled": amount}


async def send_key(cmd: dict, page: Any, system: System) -> dict:
    key = cmd.get("key", "")
    if not key or not isinstance(key, str):
        raise InputActionError("No key")
    key_hold = _bounded(cmd, "key_hold", _KEY_HOLD_BOUNDS_S)
    instant = _flag(cmd, "instant", False)
    if instant and key_hold is not None:
        raise InputActionError("key_hold cannot be used with instant")
    await asyncio.to_thread(system.send_key, key, key_hold, instant)
    return {"send_key": key}


def _int_in_range(cmd: dict, name: str, default: int, lo: int, hi: int) -> int:
    value = _integer(cmd, name, default)
    if not lo <= value <= hi:
        raise InputActionError(f"{name} must be an integer from {lo} to {hi}")
    return value


async def _pointer_into_viewport(
    page: Any, system: System
) -> tuple[int, int, int, int]:
    """Make sure wheel events land on the page; returns viewport bounds."""
    size = await page.evaluate(_VIEWPORT_JS)
    bounds = (
        _VIEWPORT_MARGIN_PX,
        _VIEWPORT_MARGIN_PX,
        int(size["w"]) - _VIEWPORT_MARGIN_PX,
        int(size["h"]) - _VIEWPORT_MARGIN_PX,
    )
    px, py = system.pointer_position()
    left, top, right, bottom = bounds
    if left <= px <= right and top <= py <= bottom:
        return bounds
    rng = system.rng
    tx = left + (right - left) * rng.uniform(*_POINTER_HOME_FRAC)
    ty = top + (bottom - top) * rng.uniform(*_POINTER_HOME_FRAC)
    log.debug("pointer outside viewport, moving onto page", extra={"x": px, "y": py})
    await asyncio.to_thread(
        system.move_mouse, tx, ty, None, size["w"] / 2, size["h"] / 2
    )
    return bounds


async def _reading_pause(
    delay: float,
    drift_p: float,
    bounds: tuple[int, int, int, int],
    system: System,
) -> None:
    pause = human_scroll.reading_pause(system.rng, delay)
    if system.rng.random() >= drift_p:
        await asyncio.sleep(pause)
        return
    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.to_thread(system.drift, bounds)
    remaining = pause - (loop.time() - started)
    if remaining > 0:
        await asyncio.sleep(remaining)


async def _scroll_to_top(page: Any, system: System) -> bool:
    is_editing = await page.evaluate(_EDITABLE_FOCUSED_JS)
    if not is_editing:
        await asyncio.to_thread(system.send_key, _KEY_HOME)
        return await _wait_for_top(page)
    # Home would move the caret instead, so flick back up with the wheel.
    for _ in range(_SCROLL_TOP_MAX_GESTURES):
        if await page.evaluate(_SCROLL_Y_JS) <= 0:
            return True
        notches = system.rng.randint(*_SCROLL_TOP_FLICK_NOTCHES)
        await asyncio.to_thread(system.scroll_gesture, notches, _WHEEL_UP)
    return await _wait_for_top(page)


async def _wait_for_top(page: Any) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SCROLL_TOP_TIMEOUT_S
    while loop.time() < deadline:
        if await page.evaluate(_SCROLL_Y_JS) <= 0:
            return True
        await asyncio.sleep(_SCROLL_TOP_POLL_S)
    return False


async def scroll_to_bottom_humanized(cmd: dict, page: Any, system: System) -> dict:
    min_clicks = _int_in_range(
        cmd, "min_clicks", _SCROLL_MIN_CLICKS_DEFAULT, 1, _SCROLL_CLICKS_LIMIT
    )
    max_clicks = _int_in_range(
        cmd, "max_clicks", _SCROLL_MAX_CLICKS_DEFAULT, 1, _SCROLL_CLICKS_LIMIT
    )
    if min_clicks > max_clicks:
        raise InputActionError("min_clicks must not exceed max_clicks")
    delay = _positive(cmd, "delay") or _SCROLL_DELAY_DEFAULT_S
    max_gestures = _int_in_range(
        cmd, "max_gestures", _SCROLL_MAX_GESTURES_DEFAULT, 1, _SCROLL_MAX_GESTURES_LIMIT
    )
    return_to_top = _flag(cmd, "return_to_top", True)
    notch_gap = _bounded(cmd, "notch_gap", _NOTCH_GAP_BOUNDS_S)
    scroll_back_p = _bounded(cmd, "scroll_back", _PROBABILITY_BOUNDS)
    if scroll_back_p is None:
        scroll_back_p = human_scroll.SCROLL_BACK_P
    drift_p = _bounded(cmd, "drift", _PROBABILITY_BOUNDS)
    if drift_p is None:
        drift_p = _SCROLL_DRIFT_P

    rng = system.rng
    await _refresh_window_offset(page, system)
    bounds = await _pointer_into_viewport(page, system)
    gestures = scroll_backs = stalls = 0
    reached_bottom = False
    while gestures < max_gestures:
        before = await page.evaluate(_SCROLL_Y_JS)
        size = human_scroll.gesture_size(rng, bounds=(min_clicks, max_clicks))
        await asyncio.to_thread(system.scroll_gesture, size, _WHEEL_DOWN, notch_gap)
        gestures += 1
        await _reading_pause(delay, drift_p, bounds, system)
        after = await page.evaluate(_SCROLL_Y_JS)
        if after <= before:
            # Lazy-loaded pages may grow after a pause; give them one more try.
            stalls += 1
            if stalls >= _SCROLL_STALLS_TO_STOP:
                reached_bottom = True
                break
            continue
        stalls = 0
        wants_back = rng.random() < scroll_back_p
        if wants_back and scroll_backs < human_scroll.SCROLL_BACK_MAX_PER_PAGE:
            notches = human_scroll.scroll_back_notches(rng)
            await asyncio.to_thread(
                system.scroll_gesture, notches, _WHEEL_UP, notch_gap
            )
            await asyncio.sleep(human_scroll.scroll_back_pause(rng, delay))
            scroll_backs += 1

    returned_to_top = False
    if return_to_top:
        returned_to_top = await _scroll_to_top(page, system)
    log.info(
        "humanized scroll finished",
        extra={
            "gestures": gestures,
            "scroll_backs": scroll_backs,
            "reached_bottom": reached_bottom,
            "returned_to_top": returned_to_top,
        },
    )
    return {
        "scrolled": "bottom_humanized",
        "gestures": gestures,
        "scroll_backs": scroll_backs,
        "reached_bottom": reached_bottom,
        "returned_to_top": returned_to_top,
    }


def _typo_block_reason(state: dict | None, text: str) -> str | None:
    """Why typos would be unsafe in the focused field, or None if safe."""
    if state is None or state["value"] is None or state["selStart"] is None:
        return _TYPO_REASON_NO_FIELD
    if state["editable"]:
        return _TYPO_REASON_CONTENTEDITABLE
    if state["tag"] == _TAG_INPUT and state["type"] not in _TYPO_SAFE_INPUT_TYPES:
        return _TYPO_REASON_FIELD_TYPE
    if state["tag"] not in (_TAG_INPUT, _TAG_TEXTAREA):
        return _TYPO_REASON_FIELD_TYPE
    if state["maxLength"] >= 0:
        return _TYPO_REASON_MAXLENGTH
    if state["hasPattern"]:
        return _TYPO_REASON_PATTERN
    if state["inputMode"] in _TYPO_UNSAFE_INPUT_MODES:
        return _TYPO_REASON_INPUTMODE
    autocomplete = state["autocomplete"]
    if any(token in autocomplete for token in _TYPO_UNSAFE_AUTOCOMPLETE):
        return _TYPO_REASON_AUTOCOMPLETE
    has_tab = "\t" in text
    has_newline_in_input = "\n" in text and state["tag"] == _TAG_INPUT
    if has_tab or has_newline_in_input:
        return _TYPO_REASON_CONTROL_CHARS
    return None


class _TypoGuard:
    """Decides whether typos are safe and checks the result afterwards."""

    def __init__(self, handle: Any, state: dict | None, reason: str | None) -> None:
        self.handle = handle
        self.state = state
        self.reason = reason

    @property
    def is_allowed(self) -> bool:
        return self.reason is None

    @classmethod
    async def inspect(cls, page: Any, text: str) -> _TypoGuard:
        handle = await page.evaluate_handle("() => document.activeElement")
        state = await handle.evaluate(_FIELD_STATE_JS)
        return cls(handle, state, _typo_block_reason(state, text))

    async def verify(self, text: str) -> None:
        state = self.state
        expected = (
            state["value"][: state["selStart"]]
            + text
            + state["value"][state["selEnd"] :]
        )
        actual = await self.handle.evaluate(_FIELD_VALUE_JS)
        if actual == expected:
            return
        log.warning(
            "typed value differs after typo corrections",
            extra={"expected_len": len(expected), "actual_len": len(actual or "")},
        )
        raise InputActionError(
            "typed value does not match the requested text after typo corrections"
        )

    async def dispose(self) -> None:
        await self.handle.dispose()


async def _typing_guard(page: Any, text: str, wants_typos: bool) -> _TypoGuard | None:
    if not wants_typos:
        return None
    guard = await _TypoGuard.inspect(page, text)
    if not guard.is_allowed:
        log.info("typos disabled for focused field", extra={"reason": guard.reason})
    return guard


def _typing_result(text: str, guard: _TypoGuard | None) -> dict:
    result: dict = {"typed_len": len(text), "typos": bool(guard and guard.is_allowed)}
    if guard is not None and not guard.is_allowed:
        result["typos_disabled_reason"] = guard.reason
    return result


def _text_param(cmd: dict) -> str:
    text = cmd.get("text", "")
    if not text or not isinstance(text, str):
        raise InputActionError("No text")
    return text


def _typing_profile(cmd: dict, system: System) -> TypingProfile | None:
    """The session's typist with any per-request overrides applied.

    interval picks the typist speed first; key_hold and rollover then
    override that speed's defaults.
    """
    interval = _positive(cmd, "interval")
    key_hold = _bounded(cmd, "key_hold", _KEY_HOLD_BOUNDS_S)
    rollover = _bounded(cmd, "rollover", _ROLLOVER_BOUNDS)
    if interval is None and key_hold is None and rollover is None:
        return None
    profile = system.typing_profile
    if interval is not None:
        profile = profile.with_median_gap(interval)
    overrides: dict[str, float] = {}
    if key_hold is not None:
        overrides["hold_median_s"] = key_hold
    if rollover is not None:
        overrides["rollover_keep_p"] = rollover
    return dataclasses.replace(profile, **overrides)


def _requested_typo_rate(cmd: dict) -> float:
    """Typo rate asked for: typo_rate if given, else the default when typos is on."""
    typo_rate = _bounded(cmd, "typo_rate", _TYPO_RATE_BOUNDS)
    wants_typos = _flag(cmd, "typos", bool(typo_rate))
    if typo_rate is None:
        return human_keyboard.DEFAULT_TYPO_RATE if wants_typos else 0.0
    if typo_rate > 0 and not wants_typos:
        raise InputActionError("typo_rate needs typos to be true")
    return typo_rate if wants_typos else 0.0


async def system_type(cmd: dict, page: Any, system: System) -> dict:
    text = _text_param(cmd)
    profile = _typing_profile(cmd, system)
    requested_rate = _requested_typo_rate(cmd)
    guard = await _typing_guard(page, text, requested_rate > 0)
    typo_rate = requested_rate if guard and guard.is_allowed else 0.0
    try:
        await asyncio.to_thread(system.system_type, text, None, typo_rate, profile)
        if typo_rate:
            await guard.verify(text)
    except InputError as e:
        raise InputActionError(str(e)) from e
    finally:
        if guard is not None:
            await guard.dispose()
    return _typing_result(text, guard)


ACTIONS: dict[str, ActionHandler] = {
    "mouse_move": mouse_move,
    "mouse_click": mouse_click,
    "system_click": system_click,
    "scroll": scroll,
    "scroll_to_bottom_humanized": scroll_to_bottom_humanized,
    "system_type": system_type,
    "send_key": send_key,
}
