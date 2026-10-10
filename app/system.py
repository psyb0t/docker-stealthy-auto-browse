"""System-level input using PyAutoGUI.

Plans come from human_mouse, human_keyboard and human_scroll; this module
replays them against the X server through XTest, timing each event against
an absolute deadline so scheduling jitter does not accumulate. Every method
blocks until its input is done; callers on the event loop run them in a
worker thread.
"""

from __future__ import annotations

import os
import random
import subprocess
import time
from collections.abc import Callable

import human_keyboard
import human_mouse
import human_scroll
from logger import get_logger

log = get_logger(__name__)

_XDOTOOL = "xdotool"
_XDOTOOL_KEYDOWN = "keydown"
_XDOTOOL_KEYUP = "keyup"
_XDOTOOL_TIMEOUT_S = 5.0

# Lateness past which a stalled replay shifts its schedule instead of
# catching up in a burst.
_MAX_LATENESS_S = 0.004

_WHEEL_UP = 1
_WHEEL_DOWN = -1

# Modifier combos: keys go down in order and come up in reverse.
_COMBO_GAP_MEDIAN_S = 0.06
_COMBO_GAP_SIGMA = 0.4
_COMBO_GAP_RANGE_S = (0.025, 0.2)
_KEY_HOLD_MEDIAN_S = 0.1
_KEY_HOLD_SIGMA = 0.3
_KEY_HOLD_RANGE_S = (0.05, 0.25)
_KEY_HOLD_CUSTOM_FLOOR_FRAC = 0.4
_KEY_HOLD_CUSTOM_CEIL_FRAC = 2.5

# Idle pointer drift while reading.
_DRIFT_RANGE_PX = (5, 60)
_DRIFT_TARGET_PX = 80.0


class InputError(Exception):
    """OS-level input could not be delivered."""


def _get_default_resolution() -> tuple[int, int]:
    """Get default resolution from XVFB_RESOLUTION env var (WxH format)."""
    xvfb_res = os.environ.get("XVFB_RESOLUTION", "1920x1080")
    parts = xvfb_res.split("x")
    width = int(parts[0]) if parts else 1920
    height = int(parts[1]) if len(parts) > 1 else 1080
    return width, height


def _unicode_keysym(char: str) -> str:
    return f"U{ord(char):04X}"


class System:
    """System-level mouse and keyboard input via PyAutoGUI."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._pyautogui = None
        self._window_offset = {"x": 0, "y": 0}
        self._rng = rng or random.Random()
        # One consistent "person" per process.
        self.mouse_profile = human_mouse.MouseProfile.sample(self._rng)
        self.typing_profile = human_keyboard.TypingProfile.sample(self._rng)

    @property
    def rng(self) -> random.Random:
        """Random source shared by every input plan in this process."""
        return self._rng

    @property
    def is_ready(self) -> bool:
        """Check if pyautogui is initialized."""
        return self._pyautogui is not None

    @property
    def window_offset(self) -> dict:
        """Get current window offset."""
        return self._window_offset

    @window_offset.setter
    def window_offset(self, value: dict) -> None:
        """Set window offset."""
        self._window_offset = value

    def init(self) -> None:
        """Initialize pyautogui after X display is available."""
        if self._pyautogui is not None:
            return

        xauth_path = os.path.expanduser("~/.Xauthority")
        if not os.path.exists(xauth_path):
            open(xauth_path, "a").close()
        os.environ.setdefault("XAUTHORITY", xauth_path)

        import pyautogui

        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0
        self._pyautogui = pyautogui
        log.info(
            "input profile ready",
            extra={
                "mouse_speed": round(self.mouse_profile.speed, 3),
                "click_hold_median_s": round(self.mouse_profile.hold_median_s, 3),
                "typing_median_gap_s": round(self.typing_profile.median_gap_s, 3),
            },
        )

    def screen_coords(self, x: float, y: float) -> tuple[float, float]:
        """Convert viewport coords to screen coords."""
        return (x + self._window_offset["x"], y + self._window_offset["y"])

    def viewport_coords(self, x: int, y: int) -> tuple[int, int]:
        """Convert screen coords to viewport coords."""
        return (x - self._window_offset["x"], y - self._window_offset["y"])

    def pointer_position(self) -> tuple[int, int]:
        """Current pointer position in viewport coords."""
        if not self._pyautogui:
            return (0, 0)
        x, y = self._pyautogui.position()
        return self.viewport_coords(int(x), int(y))

    def _post_move(self, x: int, y: int) -> None:
        # The platform call skips pyautogui's failsafe and bookkeeping, which
        # cost ~4x more per event than the XTest motion itself.
        platform = getattr(self._pyautogui, "platformModule", None)
        move = getattr(platform, "_moveTo", None)
        if move is None:
            self._pyautogui.moveTo(x, y)
            return
        move(x, y)

    def _replay(self, timeline: list[tuple[float, Callable[[], None]]]) -> None:
        # The X server stalls now and then (XTest syncs while the browser
        # repaints), either before an event is sent or inside the call that
        # sends it. An event counts as delivered when its call returns.
        # Catching up would burst events or squash key holds to a few ms, so
        # the rest of the plan shifts back and the gesture pauses instead.
        start = time.perf_counter()
        for at, action in timeline:
            due = start + at
            now = time.perf_counter()
            if now < due:
                time.sleep(due - now)
            action()
            delivered = time.perf_counter()
            if delivered - due > _MAX_LATENESS_S:
                start += delivered - due

    def _replay_path(self, path: list[tuple[float, int, int]]) -> None:
        self._replay([(t, lambda x=x, y=y: self._post_move(x, y)) for t, x, y in path])

    def move_mouse(
        self,
        x: float,
        y: float,
        duration: float | None = None,
        width: float | None = None,
        height: float | None = None,
        profile: human_mouse.MouseProfile | None = None,
    ) -> tuple[int, int]:
        """Move to a target centred on viewport (x, y).

        width and height describe the target; the pointer lands at a human
        offset inside it, never dead centre. duration rescales the move.
        profile overrides the session's mouse traits for this move.
        Returns where the pointer landed, in viewport coords.
        """
        if not self._pyautogui:
            return (round(x), round(y))

        aim_x, aim_y = human_mouse.aim_point(x, y, width, height, self._rng)
        screen_x, screen_y = self.screen_coords(aim_x, aim_y)
        start_x, start_y = (int(v) for v in self._pyautogui.position())
        path = human_mouse.plan_move(
            (start_x, start_y),
            (screen_x, screen_y),
            self._rng,
            profile or self.mouse_profile,
            target_px=human_mouse.target_extent(
                width,
                height,
                screen_x - start_x,
                screen_y - start_y,
            ),
            duration_s=duration,
            bounds=tuple(self._pyautogui.size()),
        )
        self._replay_path(path)
        landed = path[-1][1:] if path else (start_x, start_y)
        return self.viewport_coords(*landed)

    def click(
        self,
        x: float | None = None,
        y: float | None = None,
        duration: float | None = None,
        width: float | None = None,
        height: float | None = None,
        profile: human_mouse.MouseProfile | None = None,
    ) -> tuple[int, int]:
        """Click a target centred on viewport (x, y), or where the pointer is.

        With coordinates the pointer moves there first. The press has a
        human pause before it, a human hold, and the occasional 1-2 px slip.
        Returns the click position in viewport coords.
        """
        if not self._pyautogui:
            return (0, 0)
        if x is not None and y is not None:
            self.move_mouse(x, y, duration, width, height, profile)
        return self._press_release(profile or self.mouse_profile)

    def _press_release(self, profile: human_mouse.MouseProfile) -> tuple[int, int]:
        pyautogui = self._pyautogui
        plan = human_mouse.plan_click(self._rng, profile)
        time.sleep(plan.pre_press_s)
        px, py = (int(v) for v in pyautogui.position())
        pyautogui.mouseDown(px, py)
        if plan.wobble is None:
            time.sleep(plan.hold_s)
        else:
            at, dx, dy = plan.wobble
            time.sleep(at)
            self._post_move(px + dx, py + dy)
            time.sleep(max(plan.hold_s - at, 0.0))
        rx, ry = (int(v) for v in pyautogui.position())
        pyautogui.mouseUp(rx, ry)
        time.sleep(human_mouse.plan_post_release(self._rng))
        return self.viewport_coords(px, py)

    def scroll(
        self,
        amount: int,
        x: float | None = None,
        y: float | None = None,
        notch_gap: float | None = None,
    ) -> None:
        """Scroll abs(amount) wheel notches; negative scrolls down.

        Large amounts are split into gestures with short pauses. notch_gap
        sets the median seconds between notches instead of the human rhythm.
        """
        if not self._pyautogui:
            return

        if x is not None and y is not None:
            self.move_mouse(x, y)
        gaps = human_scroll.plan_scroll(amount, self._rng, notch_gap)
        self._wheel(gaps, amount)

    def scroll_gesture(
        self,
        notches: int,
        direction: int,
        notch_gap: float | None = None,
    ) -> None:
        """One wheel gesture of notches in direction (+1 up, -1 down)."""
        if not self._pyautogui:
            return
        gaps = human_scroll.plan_gesture(notches, self._rng, notch_gap)
        self._wheel(gaps, direction)

    def _wheel(self, gaps: list[float], direction: int) -> None:
        step = _WHEEL_UP if direction > 0 else _WHEEL_DOWN
        timeline = []
        at = 0.0
        for gap in gaps:
            at += gap
            timeline.append((at, lambda: self._pyautogui.scroll(step)))
        self._replay(timeline)

    def drift(self, bounds: tuple[int, int, int, int]) -> None:
        """Small idle pointer drift, kept inside viewport bounds.

        bounds is (left, top, right, bottom) in viewport coords.
        """
        if not self._pyautogui:
            return
        left, top, right, bottom = bounds
        cx, cy = self.pointer_position()
        distance = self._rng.uniform(*_DRIFT_RANGE_PX)
        dx = self._rng.uniform(-1.0, 1.0) * distance
        dy = self._rng.uniform(-1.0, 1.0) * distance
        tx = min(max(cx + dx, left), right)
        ty = min(max(cy + dy, top), bottom)
        self.move_mouse(tx, ty, width=_DRIFT_TARGET_PX, height=_DRIFT_TARGET_PX)

    def send_key(
        self,
        key: str,
        key_hold: float | None = None,
        instant: bool = False,
    ) -> None:
        """Press a key or a combo like ctrl+shift+t with human timing.

        key_hold sets the median seconds the last key is held. instant
        skips the human timing: every key goes down and up at once, which
        is fast but easy for a page to tell apart from a person.
        """
        if not self._pyautogui:
            return

        keys = key.split("+") if "+" in key else [key]
        if instant:
            self._pyautogui.hotkey(*keys)
            return
        rng = self._rng
        for name in keys[:-1]:
            self._pyautogui.keyDown(name)
            time.sleep(
                human_mouse.lognormal(
                    rng, _COMBO_GAP_MEDIAN_S, _COMBO_GAP_SIGMA, _COMBO_GAP_RANGE_S
                )
            )
        self._pyautogui.keyDown(keys[-1])
        hold_range = _KEY_HOLD_RANGE_S
        if key_hold is not None:
            hold_range = (
                key_hold * _KEY_HOLD_CUSTOM_FLOOR_FRAC,
                key_hold * _KEY_HOLD_CUSTOM_CEIL_FRAC,
            )
        time.sleep(
            human_mouse.lognormal(
                rng, key_hold or _KEY_HOLD_MEDIAN_S, _KEY_HOLD_SIGMA, hold_range
            )
        )
        for name in reversed(keys):
            self._pyautogui.keyUp(name)
            if name != keys[0]:
                time.sleep(
                    human_mouse.lognormal(
                        rng, _COMBO_GAP_MEDIAN_S, _COMBO_GAP_SIGMA, _COMBO_GAP_RANGE_S
                    )
                )

    def plan_typing(
        self,
        text: str,
        interval: float | None = None,
        typo_rate: float = 0.0,
        profile: human_keyboard.TypingProfile | None = None,
    ) -> list[human_keyboard.KeyEvent]:
        """Plan key events for text.

        interval overrides the median key gap; profile overrides the
        session's typist traits.
        """
        profile = profile or self.typing_profile
        if interval is not None and interval > 0:
            profile = profile.with_median_gap(interval)
        return human_keyboard.plan_typing(text, self._rng, profile, typo_rate)

    def system_type(
        self,
        text: str,
        interval: float | None = None,
        typo_rate: float = 0.0,
        profile: human_keyboard.TypingProfile | None = None,
    ) -> None:
        """Type text with real key events and human timing.

        Characters missing from the US layout are typed through a spare
        keycode with xdotool.
        """
        if not self._pyautogui:
            return
        events = self.plan_typing(text, interval, typo_rate, profile)
        self._replay([(e.t, lambda e=e: self._key_event(e)) for e in events])

    def _key_event(self, event: human_keyboard.KeyEvent) -> None:
        if event.is_unicode:
            self._xdotool_key(event)
            return
        if event.is_down:
            self._pyautogui.keyDown(event.key)
            return
        self._pyautogui.keyUp(event.key)

    def _xdotool_key(self, event: human_keyboard.KeyEvent) -> None:
        verb = _XDOTOOL_KEYDOWN if event.is_down else _XDOTOOL_KEYUP
        try:
            result = subprocess.run(
                [_XDOTOOL, verb, _unicode_keysym(event.key)],
                capture_output=True,
                text=True,
                timeout=_XDOTOOL_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise InputError("run xdotool") from e
        if result.returncode != 0:
            raise InputError(f"xdotool {verb} failed: {result.stderr.strip()}")

    def get_resolution(self) -> dict:
        """Get resolution from XVFB_RESOLUTION env var."""
        w, h = _get_default_resolution()
        return {"width": w, "height": h}
