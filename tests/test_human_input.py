"""Unit tests for the humanized input planners, executor and API actions.

The planners are checked statistically against published human numbers
(SapiMouse, Balabit, Aalto 136M keystrokes) with fixed seeds, so the tests
are deterministic. The executor and actions run against a fake pyautogui,
a fake clock and a fake page.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import math
import random
import statistics
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import human_keyboard
import human_mouse
import human_scroll
import input_actions
import system as system_module
from input_metrics import (
    coalesce,
    log_stats,
    pearson,
    percentile,
    skewness,
    stroke_features,
)

_SEED = 20261009
_SCREEN = (1920, 1080)
_DISTANCE_RANGE_PX = (25.0, 1300.0)
_TARGET_RANGE_PX = (16.0, 90.0)
_MOVE_SAMPLES = 3000
_CLICK_SAMPLES = 4000
_TYPING_SAMPLES = 400

_TYPING_TEXTS = (
    "The quick brown fox jumps over the lazy dog.",
    "Hello World! Is THIS ok? Yes, it's fine: 100% (mostly).",
    'user_name@example.com ~/path/to/file {a: [1, 2]} <tag> "quoted" a|b',
    "Café über naïve façade €5 £3 ñandú",
    "aaa bbb aba abba sees tool zzz",
    "line one\nline two\ttabbed",
    "Mississippi bookkeeper committee",
    "x",
)
_LEFT_HAND_LETTERS = frozenset("qwertasdfgzxcvb")
_RIGHT_HAND_LETTERS = frozenset("yuiophjklnm")


# ---------------------------------------------------------------- mouse


def _random_moves(rng: random.Random, count: int) -> list[tuple[float, dict]]:
    """(distance, features at 60 Hz) for random aimed moves."""
    out = []
    lo, hi = _DISTANCE_RANGE_PX
    for _ in range(count):
        profile = human_mouse.MouseProfile.sample(rng)
        distance = math.exp(rng.uniform(math.log(lo), math.log(hi)))
        angle = rng.uniform(0.0, 2.0 * math.pi)
        start = (960, 540)
        aim = (
            start[0] + distance * math.cos(angle),
            start[1] + distance * math.sin(angle),
        )
        path = human_mouse.plan_move(
            start, aim, rng, profile, target_px=rng.uniform(*_TARGET_RANGE_PX)
        )
        features = stroke_features(coalesce([(0.0, *start), *path]))
        if features:
            out.append((distance, features))
    return out


def test_move_lands_exactly_and_stays_on_screen() -> None:
    rng = random.Random(_SEED)
    profile = human_mouse.MouseProfile.sample(rng)
    edge_aims = [(0, 0), (_SCREEN[0] - 1, _SCREEN[1] - 1), (0, _SCREEN[1] - 1)]
    for i in range(600):
        start = (rng.randrange(_SCREEN[0]), rng.randrange(_SCREEN[1]))
        if i < len(edge_aims):
            aim = edge_aims[i]
        else:
            aim = (rng.uniform(0, _SCREEN[0] - 1), rng.uniform(0, _SCREEN[1] - 1))
        path = human_mouse.plan_move(start, aim, rng, profile, bounds=_SCREEN)
        target = (round(aim[0]), round(aim[1]))
        if not path:
            assert target == start
            continue
        assert path[-1][1:] == target
        for t, x, y in path:
            assert 0 <= x < _SCREEN[0] and 0 <= y < _SCREEN[1]
            assert t > 0
        times = [p[0] for p in path]
        assert times == sorted(times)
        positions = [p[1:] for p in path]
        assert all(a != b for a, b in zip(positions, positions[1:]))


def test_move_to_current_position_is_empty() -> None:
    rng = random.Random(_SEED)
    profile = human_mouse.MouseProfile.sample(rng)
    assert human_mouse.plan_move((300, 200), (300.2, 199.8), rng, profile) == []
    assert human_mouse.plan_move((300, 200), (300.6, 200.0), rng, profile) == [
        (0.0, 301, 200)
    ]


def test_move_event_rate_is_a_125hz_mouse() -> None:
    rng = random.Random(_SEED)
    profile = human_mouse.MouseProfile.sample(rng)
    path = human_mouse.plan_move((100, 100), (1500, 900), rng, profile)
    gaps = [b[0] - a[0] for a, b in zip(path, path[1:])]
    assert 0.007 <= statistics.median(gaps) <= 0.009
    # Not a metronome: jittered like USB polling plus dropped duplicates.
    assert statistics.pstdev(gaps) > 0.0002


def test_move_kinematics_match_human_data() -> None:
    moves = _random_moves(random.Random(_SEED), _MOVE_SAMPLES)
    features = [f for _, f in moves]
    overshoot = statistics.fmean(f["overshoot"] for f in features)
    peaks = [f["peaks"] for f in features]
    single = sum(p == 1 for p in peaks) / len(peaks)
    multi = sum(p >= 3 for p in peaks) / len(peaks)
    time_to_peak = statistics.median(f["time_to_peak"] for f in features)
    peak_to_mean = statistics.median(f["peak_to_mean"] for f in features)
    # SapiMouse aimed strokes: overshoot 35%, 1 peak 41%, >=3 peaks 19%,
    # time to peak 0.29, peak/mean speed 4.4.
    assert 0.22 <= overshoot <= 0.45, overshoot
    assert 0.25 <= single <= 0.55, single
    assert 0.08 <= multi <= 0.3, multi
    assert 0.2 <= time_to_peak <= 0.38, time_to_peak
    assert 3.2 <= peak_to_mean <= 5.5, peak_to_mean


def test_move_duration_and_straightness_follow_distance() -> None:
    moves = _random_moves(random.Random(_SEED + 1), _MOVE_SAMPLES)
    short = [f for d, f in moves if d < 60]
    long = [f for d, f in moves if d > 500]
    short_duration = statistics.median(f["duration"] for f in short)
    long_duration = statistics.median(f["duration"] for f in long)
    # SapiMouse: ~0.23 s for 20-50 px, ~0.65 s for 400+ px.
    assert 0.15 <= short_duration <= 0.35, short_duration
    assert 0.5 <= long_duration <= 0.85, long_duration
    long_straight = statistics.median(f["straightness"] for f in long)
    assert 0.85 <= long_straight < 0.99, long_straight
    distances = [math.log2(d) for d, _ in moves]
    durations = [f["duration"] for _, f in moves]
    assert pearson(distances, durations) > 0.5


def test_move_duration_override() -> None:
    rng = random.Random(_SEED)
    profile = human_mouse.MouseProfile.sample(rng)
    for duration in (0.2, 0.8, 1.5):
        path = human_mouse.plan_move(
            (0, 0), (800, 500), rng, profile, duration_s=duration
        )
        assert abs(path[-1][0] - duration) < duration * 0.05
        gaps = [b[0] - a[0] for a, b in zip(path, path[1:])]
        assert statistics.median(gaps) < 0.012


def test_aim_point_avoids_dead_centre() -> None:
    rng = random.Random(_SEED)
    dxs, dys = [], []
    for _ in range(5000):
        x, y = human_mouse.aim_point(500.0, 300.0, 120.0, 40.0, rng)
        dx, dy = x - 500.0, y - 300.0
        assert abs(dx) >= 1.0 or abs(dy) >= 1.0
        # Inset 10% (min 2 px) from each edge.
        assert abs(dx) <= 48.0 and abs(dy) <= 18.0
        dxs.append(dx)
        dys.append(dy)
    assert abs(pearson(dxs, dys)) < 0.05
    assert 12.0 <= statistics.pstdev(dxs) <= 22.0


def test_aim_point_small_and_unknown_targets() -> None:
    rng = random.Random(_SEED)
    cases = [(8.0, 8.0, 1.0), (11.0, 30.0, 1.0), (None, None, 6.0), (0.0, 0.0, 6.0)]
    for width, height, limit in cases:
        for _ in range(300):
            x, y = human_mouse.aim_point(10.0, 10.0, width, height, rng)
            assert (x, y) != (10.0, 10.0), (width, height)
            assert abs(x - 10.0) <= limit and abs(y - 10.0) <= limit, (width, height)


def test_click_timing_matches_balabit() -> None:
    rng = random.Random(_SEED)
    profile = human_mouse.MouseProfile.sample(rng)
    plans = [human_mouse.plan_click(rng, profile) for _ in range(_CLICK_SAMPLES)]
    holds = [p.hold_s for p in plans]
    assert all(0.045 <= h <= 0.26 for h in holds)
    assert 0.08 <= statistics.median(holds) <= 0.13
    presses = [p.pre_press_s for p in plans]
    assert 0.03 <= statistics.median(presses) <= 0.09
    assert percentile(presses, 0.9) > 0.15
    wobble = sum(p.wobble is not None for p in plans) / len(plans)
    assert 0.08 <= wobble <= 0.16
    for plan in plans:
        if plan.wobble is None:
            continue
        at, dx, dy = plan.wobble
        assert 0 < at < plan.hold_s
        assert 1 <= abs(dx) + abs(dy) <= 2


# ---------------------------------------------------------------- keyboard


def _typing_plans(
    typo_rate: float,
    seed: int = _SEED,
) -> list[tuple[str, list[human_keyboard.KeyEvent]]]:
    rng = random.Random(seed)
    plans = []
    for _ in range(_TYPING_SAMPLES):
        profile = human_keyboard.TypingProfile.sample(rng)
        text = rng.choice(_TYPING_TEXTS)
        events = human_keyboard.plan_typing(text, rng, profile, typo_rate)
        plans.append((text, events))
    return plans


def test_typing_always_produces_the_text() -> None:
    for typo_rate in (0.0, human_keyboard.DEFAULT_TYPO_RATE, 0.3):
        for text, events in _typing_plans(typo_rate):
            assert human_keyboard.replay_text(events) == text, (typo_rate, text)


def test_typing_empty_text_has_no_events() -> None:
    rng = random.Random(_SEED)
    profile = human_keyboard.TypingProfile.sample(rng)
    assert human_keyboard.plan_typing("", rng, profile, 0.3) == []


def test_typing_key_state_is_physically_possible() -> None:
    for text, events in _typing_plans(0.3):
        held: dict[str, float] = {}
        last_t = 0.0
        for event in events:
            assert event.t >= last_t
            last_t = event.t
            if event.is_down:
                assert event.key not in held, (text, event)
                held[event.key] = event.t
                continue
            assert event.key in held, (text, event)
            hold = event.t - held.pop(event.key)
            assert hold >= 0.015, (text, event, hold)
        assert not held, text


def test_shift_goes_down_first_with_opposite_hand() -> None:
    for text, events in _typing_plans(0.0):
        shift_down_at: dict[str, float] = {}
        is_run_start = False
        for event in events:
            if event.key in human_keyboard.SHIFT_KEYS:
                if event.is_down:
                    shift_down_at[event.key] = event.t
                    is_run_start = True
                else:
                    shift_down_at.pop(event.key)
                continue
            if not event.is_down or not shift_down_at:
                continue
            lead = event.t - max(shift_down_at.values())
            assert lead >= 0.02, (text, event, lead)
            # One Shift stays held across a run of capitals; the hand rule
            # applies to the first key of the run.
            if not is_run_start:
                continue
            is_run_start = False
            if event.key in _LEFT_HAND_LETTERS:
                assert human_keyboard.KEY_SHIFT_RIGHT in shift_down_at, (text, event)
            if event.key in _RIGHT_HAND_LETTERS:
                assert human_keyboard.KEY_SHIFT_LEFT in shift_down_at, (text, event)


def _letter_timing(
    profile: human_keyboard.TypingProfile,
    rng: random.Random,
) -> tuple[list[float], list[float], float]:
    """Gaps between presses, holds, and the share of presses with overlap."""
    text = " ".join([_TYPING_TEXTS[0].lower()] * 30)
    events = human_keyboard.plan_typing(text, rng, profile)
    downs = [e for e in events if e.is_down]
    gaps = [b.t - a.t for a, b in zip(downs, downs[1:])]
    holds = []
    open_downs: dict[str, float] = {}
    overlaps = 0
    for event in events:
        if event.is_down:
            overlaps += bool(open_downs)
            open_downs[event.key] = event.t
            continue
        holds.append(event.t - open_downs.pop(event.key))
    return gaps, holds, overlaps / len(downs)


def test_typing_gaps_are_right_skewed_lognormal() -> None:
    rng = random.Random(_SEED)
    profile = human_keyboard.TypingProfile.sample(rng)
    gaps, holds, _ = _letter_timing(profile, rng)
    assert skewness(gaps) > 1.0
    assert statistics.fmean(gaps) > statistics.median(gaps)
    _, gap_sigma = log_stats(gaps)
    assert 0.4 <= gap_sigma <= 0.9, gap_sigma
    hold_median, hold_sigma = log_stats(holds)
    assert 0.07 <= hold_median <= 0.15, hold_median
    assert 0.2 <= hold_sigma <= 0.45, hold_sigma


def test_rollover_depends_on_typist_speed() -> None:
    rng = random.Random(_SEED)
    fast = human_keyboard.TypingProfile(0.096, 0.48, 0.1, 1.0)
    slow = human_keyboard.TypingProfile(0.245, 0.66, 0.1, 0.25)
    _, _, fast_overlap = _letter_timing(fast, rng)
    _, _, slow_overlap = _letter_timing(slow, rng)
    # Aalto: ~52% rollover for very fast typists, ~3% for slow ones.
    assert fast_overlap >= 0.3, fast_overlap
    assert slow_overlap <= 0.12, slow_overlap


def test_key_pairs_change_the_gap() -> None:
    rng = random.Random(_SEED)
    profile = human_keyboard.TypingProfile(0.16, 0.57, 0.1, 0.7)

    def median_pair_gap(pair: str) -> float:
        gaps = []
        for _ in range(400):
            events = human_keyboard.plan_typing(pair, rng, profile)
            downs = [e for e in events if e.is_down]
            gaps.append(downs[1].t - downs[0].t)
        return statistics.median(gaps)

    # Frequent bigram vs same-hand vs same-finger (Aalto: "th" 0.69x, "az" 2.49x).
    assert median_pair_gap("th") < median_pair_gap("ew") < median_pair_gap("az")


def test_interval_sets_the_median_gap() -> None:
    rng = random.Random(_SEED)
    profile = human_keyboard.TypingProfile.sample(rng).with_median_gap(0.25)
    text = "asdf jkl; " * 40
    events = human_keyboard.plan_typing(text, rng, profile)
    downs = [e for e in events if e.is_down]
    gaps = [b.t - a.t for a, b in zip(downs, downs[1:])]
    assert 0.18 <= statistics.median(gaps) <= 0.32


def test_typo_rate_and_default_off() -> None:
    letters = sum(c.isalpha() for c in _TYPING_TEXTS[0]) * _TYPING_SAMPLES
    cases = ((0.0, 0.0, 0.0), (human_keyboard.DEFAULT_TYPO_RATE, 0.005, 0.04))
    for typo_rate, lo, hi in cases:
        rng = random.Random(_SEED)
        corrections = 0
        for _ in range(_TYPING_SAMPLES):
            profile = human_keyboard.TypingProfile.sample(rng)
            events = human_keyboard.plan_typing(
                _TYPING_TEXTS[0], rng, profile, typo_rate
            )
            keys = [e.key for e in events if e.is_down]
            corrections += sum(
                1
                for a, b in zip(keys, keys[1:])
                if b == human_keyboard.KEY_BACKSPACE
                and a != human_keyboard.KEY_BACKSPACE
            )
        rate = corrections / letters
        assert lo <= rate <= hi, (typo_rate, rate)


def test_unicode_keys_never_overlap() -> None:
    rng = random.Random(_SEED)
    profile = human_keyboard.TypingProfile(0.09, 0.48, 0.14, 1.0)
    for _ in range(200):
        events = human_keyboard.plan_typing("éüñ€ aé", rng, profile)
        held = 0
        for event in events:
            if event.is_unicode and event.is_down:
                assert held == 0
            held += 1 if event.is_down else -1


# ---------------------------------------------------------------- scroll


def test_scroll_notches_are_spaced_and_complete() -> None:
    rng = random.Random(_SEED)
    for notches in (1, -1, 3, 5, 12, 40, -7):
        for _ in range(100):
            gaps = human_scroll.plan_scroll(notches, rng)
            assert len(gaps) == abs(notches)
            assert gaps[0] == 0.0
            assert all(g >= human_scroll.MIN_NOTCH_GAP_S for g in gaps[1:])


def test_scroll_gestures_have_human_rhythm() -> None:
    rng = random.Random(_SEED)
    sizes = [human_scroll.gesture_size(rng) for _ in range(5000)]
    single = sum(s == 1 for s in sizes) / len(sizes)
    assert 0.2 <= single <= 0.3
    assert 2 <= statistics.median(sizes) <= 4
    assert max(sizes) <= 18
    bounded = [human_scroll.gesture_size(rng, bounds=(4, 6)) for _ in range(500)]
    assert set(bounded) <= {4, 5, 6}
    assert human_scroll.gesture_size(rng, remaining=2, bounds=(4, 6)) == 2
    gaps = [g for _ in range(2000) for g in human_scroll.plan_gesture(6, rng)[1:]]
    # Bimodal: fast flicks and slow controlled notches.
    assert percentile(gaps, 0.2) < 0.06 and percentile(gaps, 0.9) > 0.09
    long_gaps = human_scroll.plan_scroll(30, random.Random(_SEED))
    assert max(long_gaps) >= 0.15


# ---------------------------------------------------------------- executor


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def perf_counter(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)


class _FakePyAutoGUI:
    def __init__(self, clock: _FakeClock) -> None:
        self.clock = clock
        self.pos = (100, 100)
        self.calls: list[tuple] = []
        self.platformModule = SimpleNamespace(_moveTo=self._move)

    def _move(self, x: int, y: int) -> None:
        self.pos = (x, y)
        self.calls.append(("move", self.clock.now, x, y))

    def position(self) -> tuple[int, int]:
        return self.pos

    def size(self) -> tuple[int, int]:
        return _SCREEN

    def mouseDown(self, x: int, y: int) -> None:
        self.calls.append(("down", self.clock.now, x, y))

    def mouseUp(self, x: int, y: int) -> None:
        self.calls.append(("up", self.clock.now, x, y))

    def scroll(self, clicks: int) -> None:
        self.calls.append(("scroll", self.clock.now, clicks))

    def keyDown(self, key: str) -> None:
        self.calls.append(("keydown", self.clock.now, key))

    def keyUp(self, key: str) -> None:
        self.calls.append(("keyup", self.clock.now, key))

    def hotkey(self, *keys: str) -> None:
        self.calls.append(("hotkey", self.clock.now, keys))


def _fake_system() -> tuple[system_module.System, _FakePyAutoGUI, _FakeClock]:
    clock = _FakeClock()
    fake = _FakePyAutoGUI(clock)
    instance = system_module.System(random.Random(_SEED))
    instance._pyautogui = fake
    instance.window_offset = {"x": 10, "y": 80}
    return instance, fake, clock


def test_system_click_moves_then_presses_with_a_hold() -> None:
    instance, fake, clock = _fake_system()
    with patch.object(system_module, "time", clock):
        landed = instance.click(400, 300, width=120, height=40)
    kinds = [c[0] for c in fake.calls]
    assert kinds.count("move") > 10
    down = next(c for c in fake.calls if c[0] == "down")
    up = next(c for c in fake.calls if c[0] == "up")
    assert kinds.index("down") > kinds.index("move")
    assert 0.045 <= up[1] - down[1] <= 0.27
    assert landed == instance.viewport_coords(down[2], down[3])
    assert abs(landed[0] - 400) <= 48 and abs(landed[1] - 300) <= 18
    assert landed != (400, 300)


def test_send_key_instant_is_one_raw_press() -> None:
    for key, expected in (
        ("enter", ("enter",)),
        ("ctrl+shift+t", ("ctrl", "shift", "t")),
    ):
        instance, fake, clock = _fake_system()
        with patch.object(system_module, "time", clock):
            instance.send_key(key, instant=True)
        assert fake.calls == [("hotkey", 0.0, expected)]


def test_stalled_replay_pauses_instead_of_bursting() -> None:
    instance, fake, clock = _fake_system()
    stall_at_call = 10
    stall_s = 0.06
    original = fake._move

    def stalling_move(x: int, y: int) -> None:
        original(x, y)
        if len(fake.calls) == stall_at_call:
            clock.now += stall_s

    fake.platformModule._moveTo = stalling_move
    start = fake.position()
    path = human_mouse.plan_move(
        start, (1500.0, 900.0), random.Random(_SEED), instance.mouse_profile
    )
    with patch.object(system_module, "time", clock):
        instance._replay_path(path)
    posted = [c[1] for c in fake.calls]
    planned = [p[0] for p in path]
    for i in range(stall_at_call, len(posted) - 1):
        planned_gap = planned[i + 1] - planned[i]
        assert posted[i + 1] - posted[i] >= planned_gap - 1e-9, (i, planned_gap)
    # The rest of the plan moves back by the stall, minus the planned gap the
    # stall overlapped.
    stalled_gap = planned[stall_at_call] - planned[stall_at_call - 1]
    assert posted[-1] >= planned[-1] + stall_s - stalled_gap - 1e-9


def test_stall_inside_a_key_press_keeps_the_hold() -> None:
    instance, fake, clock = _fake_system()
    stall_s = 0.1
    original = fake.keyDown

    def stalling_key_down(key: str) -> None:
        clock.now += stall_s
        original(key)

    fake.keyDown = stalling_key_down
    with patch.object(system_module, "time", clock):
        instance.system_type("ok")
    down_at = {}
    holds = []
    for kind, at, key in fake.calls:
        if kind == "keydown":
            down_at[key] = at
            continue
        holds.append(at - down_at.pop(key))
    # The press reached the X server when the stalled call returned, so the
    # planned hold (at least 35 ms) still separates it from the release.
    assert len(holds) == 2
    assert min(holds) >= 0.035 - 1e-9, holds


def test_mouse_click_without_coordinates_does_not_travel() -> None:
    instance, fake, clock = _fake_system()
    with patch.object(system_module, "time", clock):
        instance.click()
    assert [c[0] for c in fake.calls if c[0] != "move"] == ["down", "up"]
    # At most the occasional 1-2 px slip while the button is down.
    for call in fake.calls:
        if call[0] == "move":
            assert abs(call[2] - 100) + abs(call[3] - 100) <= 2


def test_system_scroll_sends_single_spaced_notches() -> None:
    for amount in (-9, 1, 25):
        instance, fake, clock = _fake_system()
        with patch.object(system_module, "time", clock):
            instance.scroll(amount)
        ticks = [c for c in fake.calls if c[0] == "scroll"]
        assert len(ticks) == abs(amount)
        assert all(c[2] == (1 if amount > 0 else -1) for c in ticks)
        gaps = [b[1] - a[1] for a, b in zip(ticks, ticks[1:])]
        assert all(g >= human_scroll.MIN_NOTCH_GAP_S - 1e-9 for g in gaps)


def test_system_type_replays_holds_and_unicode() -> None:
    instance, fake, clock = _fake_system()
    xdotool_calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> SimpleNamespace:
        xdotool_calls.append(args)
        return SimpleNamespace(returncode=0, stderr="")

    with (
        patch.object(system_module, "time", clock),
        patch.object(system_module.subprocess, "run", fake_run),
    ):
        instance.system_type("Hé!")
    keys = [(c[0], c[2]) for c in fake.calls if c[0] in ("keydown", "keyup")]
    assert ("keydown", "h") in keys and ("keyup", "h") in keys
    assert ("keydown", "1") in keys
    assert any(k[1] in human_keyboard.SHIFT_KEYS for k in keys)
    assert xdotool_calls == [
        ["xdotool", "keydown", "U00E9"],
        ["xdotool", "keyup", "U00E9"],
    ]


def test_xdotool_failure_raises_input_error() -> None:
    instance, _, clock = _fake_system()

    def failing_run(args: list[str], **_: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stderr="no display")

    with (
        patch.object(system_module, "time", clock),
        patch.object(system_module.subprocess, "run", failing_run),
    ):
        try:
            instance.system_type("é")
        except system_module.InputError as error:
            assert "no display" in str(error)
        else:
            raise AssertionError("expected InputError")


def test_send_key_holds_and_releases_in_reverse() -> None:
    cases = [
        ("enter", [("keydown", "enter"), ("keyup", "enter")]),
        (
            "ctrl+shift+t",
            [
                ("keydown", "ctrl"),
                ("keydown", "shift"),
                ("keydown", "t"),
                ("keyup", "t"),
                ("keyup", "shift"),
                ("keyup", "ctrl"),
            ],
        ),
    ]
    for key, expected in cases:
        instance, fake, clock = _fake_system()
        with patch.object(system_module, "time", clock):
            instance.send_key(key)
        assert [(c[0], c[2]) for c in fake.calls] == expected
        last_down = max(i for i, c in enumerate(fake.calls) if c[0] == "keydown")
        hold = fake.calls[last_down + 1][1] - fake.calls[last_down][1]
        assert hold >= 0.05


# ---------------------------------------------------------------- actions


def _field(**overrides: object) -> dict:
    state = {
        "tag": "input",
        "type": "text",
        "editable": False,
        "maxLength": -1,
        "hasPattern": False,
        "inputMode": "",
        "autocomplete": "",
        "value": "ab",
        "selStart": 1,
        "selEnd": 1,
    }
    state.update(overrides)
    return state


def test_typo_guard_blocks_risky_fields() -> None:
    cases = [
        (_field(), "hello", None),
        (_field(type="search"), "hello", None),
        (_field(tag="textarea", type=""), "a\nb", None),
        (None, "hello", "no_focused_text_field"),
        (_field(value=None), "hello", "no_focused_text_field"),
        (_field(type="password"), "hello", "field_type"),
        (_field(type="email"), "hello", "field_type"),
        (_field(type="number"), "12", "field_type"),
        (_field(tag="select", type=""), "a", "field_type"),
        (_field(tag="div", type="", editable=True), "hello", "contenteditable"),
        (_field(maxLength=10), "hello", "maxlength"),
        (_field(hasPattern=True), "hello", "pattern"),
        (_field(inputMode="numeric"), "hello", "inputmode"),
        (_field(autocomplete="one-time-code"), "hello", "autocomplete"),
        (_field(autocomplete="cc-number"), "hello", "autocomplete"),
        (_field(autocomplete="new-password"), "hello", "autocomplete"),
        (_field(), "a\tb", "control_characters"),
        (_field(), "a\nb", "control_characters"),
    ]
    for state, text, expected in cases:
        reason = input_actions._typo_block_reason(state, text)
        assert reason == expected, (state, text, reason)


class _FakeHandle:
    """Element handle: first evaluate reads field state, later ones the value."""

    def __init__(self, state: dict | None, final_value: str | None) -> None:
        self.answers = [state, final_value]
        self.disposed = False

    async def evaluate(self, script: str) -> object:
        return self.answers.pop(0)

    async def dispose(self) -> None:
        self.disposed = True


class _FakePage:
    """Page whose evaluate() answers the window offset read."""

    def __init__(
        self,
        handle: _FakeHandle | None = None,
        offset: object = None,
    ) -> None:
        self.handle = handle
        self.offset = offset if offset is not None else {"x": 0, "y": 0}

    async def evaluate(self, script: str) -> object:
        if isinstance(self.offset, Exception):
            raise self.offset
        return self.offset

    async def evaluate_handle(self, script: str) -> _FakeHandle:
        assert self.handle is not None
        return self.handle


class _RecordingSystem:
    def __init__(self) -> None:
        self.typed: list[tuple] = []
        self.rng = random.Random(_SEED)
        self.typing_profile = human_keyboard.TypingProfile(0.16, 0.57, 0.1, 0.7)
        self.mouse_profile = human_mouse.MouseProfile(1.0, 0.1)

    def system_type(
        self,
        text: str,
        interval: float | None,
        typo_rate: float,
        profile: human_keyboard.TypingProfile | None = None,
    ) -> None:
        self.typed.append((text, interval, typo_rate, profile))


def _run_type(cmd: dict, handle: _FakeHandle | None, system: object) -> dict:
    return asyncio.run(input_actions.system_type(cmd, _FakePage(handle), system))


def test_system_type_action_applies_typos_only_where_safe() -> None:
    recording = _RecordingSystem()
    handle = _FakeHandle(_field(), "ahellob")
    result = _run_type({"text": "hello", "typos": True}, handle, recording)
    assert result == {"typed_len": 5, "typos": True}
    assert recording.typed[-1] == (
        "hello",
        None,
        human_keyboard.DEFAULT_TYPO_RATE,
        None,
    )
    assert handle.disposed

    handle = _FakeHandle(_field(type="password"), None)
    result = _run_type({"text": "hello", "typos": True}, handle, recording)
    assert result == {
        "typed_len": 5,
        "typos": False,
        "typos_disabled_reason": "field_type",
    }
    assert recording.typed[-1][2] == 0.0
    assert handle.disposed

    result = _run_type({"text": "hello", "interval": "0.2"}, None, recording)
    assert result == {"typed_len": 5, "typos": False}
    text, interval, typo_rate, profile = recording.typed[-1]
    assert (text, interval, typo_rate) == ("hello", None, 0.0)
    assert profile.median_gap_s == 0.2


def test_system_type_action_reports_a_wrong_final_value() -> None:
    handle = _FakeHandle(_field(), "ahelxob")
    try:
        _run_type({"text": "hello", "typos": True}, handle, _RecordingSystem())
    except input_actions.InputActionError as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("expected a value mismatch error")
    assert handle.disposed


def test_action_parameter_validation() -> None:
    recording = _RecordingSystem()
    cases = [
        (input_actions.mouse_move, {"x": 1}, "y is required"),
        (input_actions.mouse_move, {"x": "a", "y": 2}, "x must be a number"),
        (input_actions.mouse_move, {"x": True, "y": 2}, "x must be a number"),
        (
            input_actions.mouse_move,
            {"x": 1, "y": 2, "w": 0},
            "w must be greater than 0",
        ),
        (input_actions.system_click, {}, "x is required"),
        (input_actions.mouse_click, {"x": 5}, "x and y must be given together"),
        (input_actions.scroll, {"amount": 0}, "amount must be a non-zero integer"),
        (input_actions.scroll, {"amount": 1.5}, "amount must be an integer"),
        (input_actions.system_type, {"text": ""}, "No text"),
        (
            input_actions.system_type,
            {"text": "a", "typos": "yes"},
            "typos must be true or false",
        ),
        (
            input_actions.system_type,
            {"text": "a", "interval": -1},
            "interval must be greater than 0",
        ),
        (input_actions.send_key, {}, "No key"),
        (
            input_actions.scroll_to_bottom_humanized,
            {"min_clicks": 7, "max_clicks": 3},
            "min_clicks must not exceed max_clicks",
        ),
        (
            input_actions.scroll_to_bottom_humanized,
            {"max_clicks": 51},
            "max_clicks must be an integer from 1 to 50",
        ),
        (
            input_actions.scroll_to_bottom_humanized,
            {"return_to_top": "no"},
            "return_to_top must be true or false",
        ),
    ]
    for handler, cmd, message in cases:
        try:
            asyncio.run(handler(cmd, _FakePage(), recording))
        except input_actions.InputActionError as error:
            assert str(error) == message, (cmd, str(error))
            continue
        raise AssertionError(f"{handler.__name__} accepted {cmd}")


def test_mouse_overrides_change_the_motion() -> None:
    base = human_mouse.MouseProfile(1.0, 0.1)

    def median_duration(profile: human_mouse.MouseProfile) -> float:
        rng = random.Random(_SEED)
        return statistics.median(
            human_mouse.plan_move((0, 0), (900, 300), rng, profile)[-1][0]
            for _ in range(300)
        )

    def median_straightness(profile: human_mouse.MouseProfile) -> float:
        rng = random.Random(_SEED)
        values = []
        for _ in range(300):
            path = human_mouse.plan_move((0, 0), (900, 300), rng, profile)
            features = stroke_features([(0.0, 0, 0), *path])
            values.append(features["straightness"])
        return statistics.median(values)

    fast = dataclasses.replace(base, speed=2.0)
    assert median_duration(fast) < median_duration(base) * 0.6
    straight = dataclasses.replace(base, curvature=0.0, tremor_px=0.0)
    curvy = dataclasses.replace(base, curvature=3.0)
    assert (
        median_straightness(curvy)
        < median_straightness(base)
        < median_straightness(straight)
    )

    rng = random.Random(_SEED)
    slow_press = dataclasses.replace(base, hold_median_s=0.6, press_median_s=1.5)
    plans = [human_mouse.plan_click(rng, slow_press) for _ in range(500)]
    assert 0.45 <= statistics.median(p.hold_s for p in plans) <= 0.75
    assert 1.1 <= statistics.median(p.pre_press_s for p in plans) <= 1.9


def test_typing_overrides_change_holds_and_rollover() -> None:
    rng = random.Random(_SEED)
    base = human_keyboard.TypingProfile(0.096, 0.48, 0.1, 1.0)
    long_holds = dataclasses.replace(base, hold_median_s=0.3)
    _, holds, _ = _letter_timing(long_holds, rng)
    assert 0.24 <= statistics.median(holds) <= 0.36
    no_rollover = dataclasses.replace(base, rollover_keep_p=0.0)
    _, _, overlap = _letter_timing(no_rollover, rng)
    _, _, default_overlap = _letter_timing(base, rng)
    assert overlap < default_overlap / 3


def test_scroll_notch_gap_override() -> None:
    rng = random.Random(_SEED)
    gaps = [g for _ in range(200) for g in human_scroll.plan_gesture(6, rng, 0.5)[1:]]
    assert 0.4 <= statistics.median(gaps) <= 0.6
    assert min(gaps) >= 0.25


class _OverrideSystem:
    """Records the profile and options each pointer, wheel and key call gets."""

    def __init__(self) -> None:
        self.mouse_profile = human_mouse.MouseProfile(1.2, 0.1)
        self.typing_profile = human_keyboard.TypingProfile(0.16, 0.57, 0.1, 0.7)
        self.rng = random.Random(_SEED)
        self.window_offset = {"x": 0, "y": 0}
        self.calls: list[tuple] = []

    def move_mouse(self, *args: object) -> tuple[int, int]:
        self.calls.append(("move", *args))
        return (1, 1)

    def click(self, *args: object) -> tuple[int, int]:
        self.calls.append(("click", *args))
        return (1, 1)

    def scroll(self, *args: object) -> None:
        self.calls.append(("scroll", *args))

    def send_key(self, *args: object) -> None:
        self.calls.append(("key", *args))

    def system_type(self, *args: object) -> None:
        self.calls.append(("type", *args))


def test_actions_pass_overrides_through() -> None:
    fake = _OverrideSystem()
    asyncio.run(
        input_actions.system_click(
            {
                "x": 5,
                "y": 5,
                "speed": 2,
                "curvature": 0.5,
                "tremor": 0,
                "click_hold": 0.3,
                "click_delay": 0.8,
            },
            _FakePage(),
            fake,
        )
    )
    profile = fake.calls[-1][-1]
    assert profile == human_mouse.MouseProfile(2.4, 0.3, 0.5, 0.0, 0.8)

    asyncio.run(input_actions.mouse_move({"x": 5, "y": 5}, _FakePage(), fake))
    assert fake.calls[-1][-1] is None

    asyncio.run(
        input_actions.scroll({"amount": -4, "notch_gap": 0.2}, _FakePage(), fake)
    )
    assert fake.calls[-1] == ("scroll", -4, None, None, 0.2)

    asyncio.run(input_actions.send_key({"key": "tab", "key_hold": 0.4}, None, fake))
    assert fake.calls[-1] == ("key", "tab", 0.4, False)

    asyncio.run(input_actions.send_key({"key": "ctrl+a", "instant": True}, None, fake))
    assert fake.calls[-1] == ("key", "ctrl+a", None, True)

    asyncio.run(
        input_actions.system_type(
            {"text": "hi", "interval": 0.3, "key_hold": 0.2, "rollover": 0.1},
            _FakePage(),
            fake,
        )
    )
    _, text, interval, typo_rate, typing = fake.calls[-1]
    assert (text, interval, typo_rate) == ("hi", None, 0.0)
    assert typing.median_gap_s == 0.3
    assert typing.hold_median_s == 0.2
    assert typing.rollover_keep_p == 0.1

    handle = _FakeHandle(_field(value="", selStart=0, selEnd=0), "hi")
    asyncio.run(
        input_actions.system_type(
            {"text": "hi", "typo_rate": 0.1}, _FakePage(handle), fake
        )
    )
    assert fake.calls[-1][3] == 0.1


def test_override_validation() -> None:
    fake = _OverrideSystem()
    cases = [
        (
            input_actions.system_click,
            {"x": 1, "y": 1, "speed": 0},
            "speed must be between 0.1 and 10.0",
        ),
        (
            input_actions.mouse_move,
            {"x": 1, "y": 1, "curvature": 9},
            "curvature must be between 0.0 and 5.0",
        ),
        (
            input_actions.mouse_click,
            {"tremor": -1},
            "tremor must be between 0.0 and 5.0",
        ),
        (
            input_actions.mouse_click,
            {"click_hold": 0},
            "click_hold must be between 0.01 and 5.0",
        ),
        (
            input_actions.mouse_click,
            {"click_delay": 11},
            "click_delay must be between 0.0 and 10.0",
        ),
        (
            input_actions.scroll,
            {"notch_gap": 0.001},
            "notch_gap must be between 0.015 and 5.0",
        ),
        (
            input_actions.send_key,
            {"key": "a", "key_hold": 3},
            "key_hold must be between 0.01 and 2.0",
        ),
        (
            input_actions.send_key,
            {"key": "a", "instant": True, "key_hold": 0.1},
            "key_hold cannot be used with instant",
        ),
        (
            input_actions.send_key,
            {"key": "a", "instant": "yes"},
            "instant must be true or false",
        ),
        (
            input_actions.system_type,
            {"text": "a", "rollover": 2},
            "rollover must be between 0.0 and 1.0",
        ),
        (
            input_actions.system_type,
            {"text": "a", "typo_rate": 0.5},
            "typo_rate must be between 0.0 and 0.2",
        ),
        (
            input_actions.system_type,
            {"text": "a", "typo_rate": 0.1, "typos": False},
            "typo_rate needs typos to be true",
        ),
        (
            input_actions.scroll_to_bottom_humanized,
            {"scroll_back": 1.5},
            "scroll_back must be between 0.0 and 1.0",
        ),
        (
            input_actions.scroll_to_bottom_humanized,
            {"drift": -0.1},
            "drift must be between 0.0 and 1.0",
        ),
    ]
    for handler, cmd, message in cases:
        try:
            asyncio.run(handler(cmd, _FakePage(), fake))
        except input_actions.InputActionError as error:
            assert str(error) == message, (cmd, str(error))
            continue
        raise AssertionError(f"{handler.__name__} accepted {cmd}")


def test_click_at_zero_coordinates_moves_there() -> None:
    calls: list[tuple] = []

    class ClickSystem:
        window_offset = {"x": 0, "y": 0}

        def click(self, *args: object) -> tuple[int, int]:
            calls.append(args)
            return (1, 1)

    result = asyncio.run(
        input_actions.mouse_click({"x": 0, "y": "0"}, _FakePage(), ClickSystem())
    )
    assert calls[0][:2] == (0.0, 0.0)
    assert result == {"clicked_at": {"x": 0, "y": "0"}, "landed_at": {"x": 1, "y": 1}}


def test_pointer_actions_remeasure_the_window_offset() -> None:
    moved = {"x": 0, "y": 0}

    class OffsetSystem(_OverrideSystem):
        def click(self, *args: object) -> tuple[int, int]:
            self.calls.append(("click", dict(self.window_offset)))
            return (1, 1)

        def move_mouse(self, *args: object) -> tuple[int, int]:
            self.calls.append(("move", dict(self.window_offset)))
            return (1, 1)

        def scroll(self, *args: object) -> None:
            self.calls.append(("scroll", dict(self.window_offset)))

    cases = [
        (input_actions.system_click, {"x": 5, "y": 5}),
        (input_actions.mouse_click, {}),
        (input_actions.mouse_move, {"x": 5, "y": 5}),
        (input_actions.scroll, {"amount": -2, "x": 5, "y": 5}),
    ]
    for handler, cmd in cases:
        fake = OffsetSystem()
        fake.window_offset = {"x": 0, "y": 80}
        asyncio.run(handler(cmd, _FakePage(offset=moved), fake))
        assert fake.calls[-1][1] == moved, (handler.__name__, fake.calls)


def test_unreadable_offset_keeps_the_last_good_one() -> None:
    last_good = {"x": 12, "y": 90}
    unreadable = [
        input_actions.PlaywrightError("Target page closed"),
        {"x": None, "y": None},
        "not a dict",
    ]
    for answer in unreadable:
        fake = _OverrideSystem()
        fake.window_offset = dict(last_good)
        asyncio.run(
            input_actions.system_click({"x": 5, "y": 5}, _FakePage(offset=answer), fake)
        )
        assert fake.window_offset == last_good, answer

    page = _FakePage(offset={"x": 3.6, "y": 120.2})
    assert asyncio.run(input_actions.read_window_offset(page)) == {"x": 3, "y": 120}


def main_test() -> None:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(json.dumps({"result": "human input tests passed", "tests": len(tests)}))


if __name__ == "__main__":
    main_test()
