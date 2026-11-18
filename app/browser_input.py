"""The browser viewport as the input surface, and the typo guard.

auto-linux-desk's humanized input actions take coordinates relative to the
active surface. Stealthy installs this one, so coordinates start at the top
left of the page viewport, wherever the browser window sits on screen, and
the scroll and typing helpers read the page.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from autolinuxdesk.input_actions import InputActionError
from autolinuxdesk.logger import get_logger
from autolinuxdesk.surface import Surface
from autolinuxdesk.system import System
from playwright.async_api import Error as PlaywrightError

log = get_logger(__name__)


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


GetPage = Callable[[], Awaitable[Any]]


class BrowserSurface(Surface):
    """The active page's viewport."""

    name = "browser_viewport"

    def __init__(self, get_page: GetPage) -> None:
        self._get_page = get_page

    async def refresh_offset(self, system: System) -> None:
        """Re-measure where the page sits on screen before moving the pointer.

        Fullscreen, new tab windows and browser relaunches move the viewport,
        so a stored offset goes stale. The last good offset is kept when the
        page cannot be read.
        """
        page = await self._get_page()
        if page is None:
            return
        offset = await read_window_offset(page)
        if offset is None:
            return
        if offset != system.window_offset:
            log.debug(
                "window offset changed",
                extra={"old": system.window_offset, "new": offset},
            )
        system.window_offset = offset

    async def size(self, system: System) -> dict[str, int]:
        page = await self._get_page()
        return await page.evaluate(_VIEWPORT_JS)

    async def scroll_y(self) -> float | None:
        page = await self._get_page()
        return await page.evaluate(_SCROLL_Y_JS)

    async def editable_focused(self) -> bool:
        page = await self._get_page()
        return await page.evaluate(_EDITABLE_FOCUSED_JS)

    async def typing_guard(self, text: str) -> _TypoGuard:
        page = await self._get_page()
        guard = await _TypoGuard.inspect(page, text)
        if not guard.is_allowed:
            log.info("typos disabled for focused field", extra={"reason": guard.reason})
        return guard
