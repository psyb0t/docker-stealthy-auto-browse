"""Human-like pointer trajectories and click timing.

A move is the sum of overlapping sigma-lognormal strokes (Plamondon's
kinematic theory of rapid movements): an optional slow drift before the main
movement, a curved primary stroke that deliberately lands a little off, an
optional mid-course re-aim on longer moves, and one or two homing corrections
that close the remaining distance exactly. Total movement time follows Fitts's
law with lognormal trial-to-trial variation.

The planners are pure. They return timestamped integer positions and timing
values and never touch the display, so they can be seeded and unit tested.
System replays the plans against the X server.

The defaults were fitted by random search to aimed strokes in the SapiMouse
dataset (browser mousemove, 120 users): overshoot rate, velocity peak count
shares, time to peak velocity, peak to mean speed, and per-distance duration
and straightness. Click timing follows the Balabit dataset. The tremor and
event cadence model a 125 Hz USB mouse; Firefox coalesces page mousemove events
to its refresh rate, which is what pages and detectors observe.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# Fitts's law for the whole movement, corrections included: MT = a + b*ID.
_FITTS_A_S = 0.082
_FITTS_B_S_PER_BIT = 0.129
_MT_LOG_SIGMA = 0.29
_DEFAULT_TARGET_PX = 30.0
_MIN_TARGET_PX = 4.0
_MIN_MOVE_PX = 1.0

_PRIMARY_TIME_FRAC = (0.745, 0.863)
_PRIMARY_SIGMA = (0.35, 0.45)
_PRIMARY_GAIN_MU = 0.917
# Gain SD = base + scale / D^exponent. A fixed absolute endpoint error makes
# short moves relatively less accurate, which keeps the overshoot rate
# roughly flat across distances as in human data.
_PRIMARY_GAIN_SD_BASE = 0.035
_PRIMARY_GAIN_SD_SCALE = 3.7
_PRIMARY_GAIN_SD_EXPONENT = 0.75
_PRIMARY_ANGLE_SD_RAD = 0.04
# Half the turning angle of the primary arc. Short moves curve more and long
# moves are straighter, as in human data.
_ARC_DELTA_SD_RAD = 0.49
_ARC_REF_PX = 200.0
_ARC_EXPONENT = 0.76
_ARC_SCALE_RANGE = (0.35, 1.5)

_CORR_SIGMA = (0.3, 0.45)
_CORR_DURATION_S = (0.074, 0.104)
_CORR_ONSET_FRAC = (0.68, 0.93)
_CORR_DELTA_SD_RAD = 0.2
_CORR_MIN_RESIDUAL_PX = 0.5
# Short moves get shorter corrections: scale = min(1, base + D / span).
_CORR_SHORT_SCALE_BASE = 0.45
_CORR_SHORT_SCALE_SPAN_PX = 240.0
# Sometimes the first correction is itself mis-scaled and a second finishes.
_CORR2_P = 0.635
_CORR2_MIN_RESIDUAL_PX = 4.0
_CORR2_SPLIT = (0.395, 0.935)
_CORR2_ONSET_FRAC = (0.99, 1.455)

_EXTRA_P = 0.706
_EXTRA_MIN_PX = 150.0
_EXTRA_ONSET_FRAC = (0.3, 0.6)
_EXTRA_DURATION_FRAC = 0.5
_EXTRA_AMP_FRAC = 0.223
# Re-aim amplitude grows with sqrt(D), so long moves stay straight and do not
# overshoot more often than short ones.
_EXTRA_AMP_EXPONENT = 0.5

_PRE_P = 0.6
_PRE_AMP_FRAC = 0.044
_PRE_AMP_SPREAD = (0.5, 1.5)
_PRE_LEAD_S = (0.05, 0.2)
_PRE_OVERLAP_FRAC = 0.3

TREMOR_PX = 0.35
_TREMOR_TAU_S = 0.03
# Tremor fades out over the final part of the move so the landing is clean.
_TREMOR_FADE_FRAC = 0.1
EVENT_INTERVAL_S = 0.008
_EVENT_JITTER_S = 0.0004
_FINAL_SETTLE_EPS_S = 1e-6

# Click aim: per-axis truncated Gaussian around the target centre.
_CLICK_SD_DIV_X = 6.0
_CLICK_SD_DIV_Y = 5.0
_CLICK_INSET_FRAC = 0.1
_CLICK_INSET_MIN_PX = 2.0
_CLICK_SMALL_BOX_PX = 12.0
# Unknown target size: assume a small control so the aim stays on it.
_CLICK_DEFAULT_BOX_PX = 16.0
_CLICK_MIN_OFFSET_PX = 1.0
_CLICK_MIN_LIMIT_PX = 1.5
_CLICK_FALLBACK_OFFSET_PX = 1.5
_CLICK_MAX_REDRAWS = 32

# Delay from the last movement event to mousedown (Balabit: median ~30 ms,
# heavy tail when the user checks the target first).
_PRESS_FAST_P = 0.65
_PRESS_FAST_MEDIAN_S = 0.04
_PRESS_FAST_SIGMA = 0.6
_PRESS_FAST_RANGE_S = (0.008, 0.2)
_PRESS_SLOW_MEDIAN_S = 0.3
_PRESS_SLOW_SIGMA = 0.7
_PRESS_SLOW_RANGE_S = (0.12, 2.5)
_PRESS_CUSTOM_RANGE_S = (0.0, 10.0)

# Button hold (Balabit fit: median 103 ms, sigma 0.357).
_HOLD_MEDIAN_RANGE_S = (0.085, 0.125)
_HOLD_SIGMA = 0.33
_HOLD_RANGE_S = (0.045, 0.26)
# A caller-chosen hold median widens the clip range around it.
_HOLD_CUSTOM_FLOOR_FRAC = 0.4
_HOLD_CUSTOM_CEIL_FRAC = 2.5
# About 15% of human clicks move 1-2 px while the button is down.
_WOBBLE_P = 0.12
_WOBBLE_STEPS_PX = (-2, -1, 1, 2)
_WOBBLE_AT_FRAC = (0.2, 0.8)

_POST_RELEASE_MEDIAN_S = 0.15
_POST_RELEASE_SIGMA = 0.6
_POST_RELEASE_RANGE_S = (0.04, 0.6)

_SPEED_RANGE = (0.85, 1.15)


@dataclass(frozen=True)
class MouseProfile:
    """Per-session traits, so one session moves like one consistent person.

    speed divides the time of the whole move (2.0 moves twice as fast). curvature scales
    how much paths bend and tremor_px is the hand tremor amplitude.
    hold_median_s is the median button hold; press_median_s, when set,
    replaces the default pause before mousedown with one around it.
    """

    speed: float
    hold_median_s: float
    curvature: float = 1.0
    tremor_px: float = TREMOR_PX
    press_median_s: float | None = None

    @classmethod
    def sample(cls, rng: random.Random) -> MouseProfile:
        return cls(
            speed=rng.uniform(*_SPEED_RANGE),
            hold_median_s=rng.uniform(*_HOLD_MEDIAN_RANGE_S),
        )


@dataclass(frozen=True)
class ClickPlan:
    """Timing for one click after the pointer has arrived."""

    pre_press_s: float
    hold_s: float
    # (seconds after mousedown, dx, dy) for an occasional 1-2 px slip.
    wobble: tuple[float, int, int] | None


def lognormal(
    rng: random.Random,
    median: float,
    sigma: float,
    bounds: tuple[float, float],
) -> float:
    """Clipped lognormal sample around a median."""
    lo, hi = bounds
    return min(hi, max(lo, median * math.exp(rng.gauss(0.0, sigma))))


class _Stroke:
    """One sigma-lognormal stroke along a circular arc."""

    def __init__(
        self,
        onset_s: float,
        duration_s: float,
        sigma: float,
        displacement: tuple[float, float],
        delta: float,
    ) -> None:
        self.sigma = sigma
        # Put ~99.7% of the lognormal inside [onset, onset + duration].
        spread = math.exp(3 * sigma) - math.exp(-3 * sigma)
        self.mu = math.log(max(duration_s, 1e-3) / spread)
        self.t0 = onset_s - math.exp(self.mu - 3 * sigma)
        self.t_end = self.t0 + math.exp(self.mu + 3 * sigma)
        dx, dy = displacement
        self.dx = dx
        self.dy = dy
        self.chord = math.hypot(dx, dy)
        self.theta0 = math.atan2(dy, dx)
        self.delta = delta if abs(delta) > 1e-6 else 0.0
        self.length = (
            self.chord * self.delta / math.sin(self.delta) if self.delta else self.chord
        )

    def _fraction(self, t: float) -> float:
        dt = t - self.t0
        if dt <= 0:
            return 0.0
        z = (math.log(dt) - self.mu) / (self.sigma * math.sqrt(2.0))
        return 0.5 * (1.0 + math.erf(z))

    def offset(self, t: float) -> tuple[float, float]:
        s = self._fraction(t)
        if not self.delta:
            return (self.dx * s, self.dy * s)
        # Direction turns linearly from theta0 + delta to theta0 - delta.
        start = self.theta0 + self.delta
        turn = -2.0 * self.delta
        x = self.length * (math.sin(start + turn * s) - math.sin(start)) / turn
        y = self.length * (math.cos(start) - math.cos(start + turn * s)) / turn
        return (x, y)


def _arc_delta_sd(distance: float) -> float:
    scale = (_ARC_REF_PX / max(distance, _MIN_MOVE_PX)) ** _ARC_EXPONENT
    lo, hi = _ARC_SCALE_RANGE
    return _ARC_DELTA_SD_RAD * min(hi, max(lo, scale))


def _primary_gain_sd(distance: float) -> float:
    return (
        _PRIMARY_GAIN_SD_BASE
        + _PRIMARY_GAIN_SD_SCALE
        / max(distance, _MIN_MOVE_PX) ** _PRIMARY_GAIN_SD_EXPONENT
    )


def _plan_strokes(
    dx: float,
    dy: float,
    target_px: float,
    rng: random.Random,
    profile: MouseProfile,
) -> list[_Stroke]:
    distance = math.hypot(dx, dy)
    index_of_difficulty = math.log2(distance / target_px + 1.0)
    mt = (_FITTS_A_S + _FITTS_B_S_PER_BIT * index_of_difficulty) * math.exp(
        rng.gauss(0.0, _MT_LOG_SIGMA)
    )
    primary_s = mt * rng.uniform(*_PRIMARY_TIME_FRAC)

    gain = rng.gauss(_PRIMARY_GAIN_MU, _primary_gain_sd(distance))
    angle = rng.gauss(0.0, _PRIMARY_ANGLE_SD_RAD)
    pdx = gain * (dx * math.cos(angle) - dy * math.sin(angle))
    pdy = gain * (dx * math.sin(angle) + dy * math.cos(angle))

    lead = rng.uniform(*_PRE_LEAD_S) if rng.random() < _PRE_P else 0.0
    primary = _Stroke(
        lead,
        primary_s,
        rng.uniform(*_PRIMARY_SIGMA),
        (pdx, pdy),
        rng.gauss(0.0, _arc_delta_sd(distance) * profile.curvature),
    )
    strokes = [primary]

    if lead:
        frac = _PRE_AMP_FRAC * rng.uniform(*_PRE_AMP_SPREAD)
        lateral = frac * distance / 2.0
        strokes.append(
            _Stroke(
                0.0,
                lead + primary_s * _PRE_OVERLAP_FRAC,
                rng.uniform(*_CORR_SIGMA),
                (
                    frac * dx + rng.gauss(0.0, lateral),
                    frac * dy + rng.gauss(0.0, lateral),
                ),
                0.0,
            )
        )

    if distance > _EXTRA_MIN_PX and rng.random() < _EXTRA_P:
        amp = (
            _EXTRA_AMP_FRAC
            * _EXTRA_MIN_PX
            * (distance / _EXTRA_MIN_PX) ** _EXTRA_AMP_EXPONENT
        )
        strokes.append(
            _Stroke(
                lead + primary_s * rng.uniform(*_EXTRA_ONSET_FRAC),
                primary_s * _EXTRA_DURATION_FRAC,
                rng.uniform(*_CORR_SIGMA),
                (rng.gauss(0.0, amp), rng.gauss(0.0, amp)),
                0.0,
            )
        )

    rx = dx - sum(s.dx for s in strokes)
    ry = dy - sum(s.dy for s in strokes)
    residual = math.hypot(rx, ry)
    if residual < _CORR_MIN_RESIDUAL_PX:
        return strokes

    corr_scale = min(
        1.0,
        _CORR_SHORT_SCALE_BASE + distance / _CORR_SHORT_SCALE_SPAN_PX,
    )
    onset = lead + (primary.t_end - primary.t0) * rng.uniform(*_CORR_ONSET_FRAC)
    corr_s = rng.uniform(*_CORR_DURATION_S) * corr_scale
    is_split = residual > _CORR2_MIN_RESIDUAL_PX and rng.random() < _CORR2_P
    if not is_split:
        strokes.append(
            _Stroke(
                onset,
                corr_s,
                rng.uniform(*_CORR_SIGMA),
                (rx, ry),
                rng.gauss(0.0, _CORR_DELTA_SD_RAD),
            )
        )
        return strokes

    k = rng.uniform(*_CORR2_SPLIT)
    strokes.append(
        _Stroke(
            onset,
            corr_s,
            rng.uniform(*_CORR_SIGMA),
            (k * rx, k * ry),
            rng.gauss(0.0, _CORR_DELTA_SD_RAD),
        )
    )
    strokes.append(
        _Stroke(
            onset + corr_s * rng.uniform(*_CORR2_ONSET_FRAC),
            rng.uniform(*_CORR_DURATION_S) * corr_scale,
            rng.uniform(*_CORR_SIGMA),
            ((1.0 - k) * rx, (1.0 - k) * ry),
            0.0,
        )
    )
    return strokes


def plan_move(
    start: tuple[int, int],
    aim: tuple[float, float],
    rng: random.Random,
    profile: MouseProfile,
    target_px: float | None = None,
    duration_s: float | None = None,
    bounds: tuple[int, int] | None = None,
) -> list[tuple[float, int, int]]:
    """Plan a move from start to aim.

    Returns (seconds from start, x, y) samples at ~125 Hz, only where the
    pixel changes, ending exactly on the rounded aim point. target_px is
    the target size along the approach direction (Fitts's law width).
    duration_s, when given, rescales the timing without changing the event
    rate. bounds is the (width, height) of the screen to clamp to.
    """
    sx, sy = start
    target = _clamp((round(aim[0]), round(aim[1])), bounds)
    dx, dy = aim[0] - sx, aim[1] - sy
    if math.hypot(dx, dy) < _MIN_MOVE_PX:
        return [(0.0, target[0], target[1])] if target != (sx, sy) else []

    width = max(target_px or _DEFAULT_TARGET_PX, _MIN_TARGET_PX)
    strokes = _plan_strokes(dx, dy, width, rng, profile)
    natural_end = max(s.t_end for s in strokes)
    # speed scales the whole movement; an explicit duration wins over it.
    time_scale = 1.0 / profile.speed
    if duration_s is not None and duration_s > 0:
        time_scale = duration_s / natural_end
    end = natural_end * time_scale

    decay = math.exp(-EVENT_INTERVAL_S / _TREMOR_TAU_S)
    drive = math.sqrt(1.0 - decay * decay)
    noise_x = noise_y = 0.0
    samples: list[tuple[float, int, int]] = []
    last = (sx, sy)
    t = EVENT_INTERVAL_S + rng.gauss(0.0, _EVENT_JITTER_S)
    while t < end:
        noise_x = decay * noise_x + drive * rng.gauss(0.0, profile.tremor_px)
        noise_y = decay * noise_y + drive * rng.gauss(0.0, profile.tremor_px)
        local = t / time_scale
        ox = oy = 0.0
        for stroke in strokes:
            sdx, sdy = stroke.offset(local)
            ox += sdx
            oy += sdy
        fade = min(1.0, (end - t) / (_TREMOR_FADE_FRAC * end))
        point = _clamp(
            (round(sx + ox + fade * noise_x), round(sy + oy + fade * noise_y)),
            bounds,
        )
        if point != last:
            samples.append((t, point[0], point[1]))
            last = point
        t += EVENT_INTERVAL_S + rng.gauss(0.0, _EVENT_JITTER_S)
    if target != last:
        samples.append((end + _FINAL_SETTLE_EPS_S, target[0], target[1]))
    return samples


def _clamp(
    point: tuple[int, int],
    bounds: tuple[int, int] | None,
) -> tuple[int, int]:
    if not bounds:
        return point
    width, height = bounds
    return (min(max(point[0], 0), width - 1), min(max(point[1], 0), height - 1))


def aim_point(
    cx: float,
    cy: float,
    width: float | None,
    height: float | None,
    rng: random.Random,
) -> tuple[float, float]:
    """Pick where inside a target box centred on (cx, cy) to land.

    Humans scatter around the centre and almost never hit it to the pixel,
    which some detectors check. The result is never within 1 px of the
    centre on both axes. Unknown sizes are treated as a small control.
    """
    w = width if width and width > 0 else _CLICK_DEFAULT_BOX_PX
    h = height if height and height > 0 else _CLICK_DEFAULT_BOX_PX
    if min(w, h) < _CLICK_SMALL_BOX_PX:
        offsets = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
        dx, dy = rng.choice(offsets)
        return (cx + dx, cy + dy)

    half_x = w / 2.0 - max(_CLICK_INSET_MIN_PX, w * _CLICK_INSET_FRAC)
    half_y = h / 2.0 - max(_CLICK_INSET_MIN_PX, h * _CLICK_INSET_FRAC)
    for _ in range(_CLICK_MAX_REDRAWS):
        dx = _truncated_gauss(rng, w / _CLICK_SD_DIV_X, half_x)
        dy = _truncated_gauss(rng, h / _CLICK_SD_DIV_Y, half_y)
        if abs(dx) >= _CLICK_MIN_OFFSET_PX or abs(dy) >= _CLICK_MIN_OFFSET_PX:
            return (cx + dx, cy + dy)
    offsets = (-_CLICK_FALLBACK_OFFSET_PX, _CLICK_FALLBACK_OFFSET_PX)
    return (cx + rng.choice(offsets), cy + rng.choice(offsets))


def _truncated_gauss(rng: random.Random, sd: float, limit: float) -> float:
    limit = max(limit, _CLICK_MIN_LIMIT_PX)
    for _ in range(_CLICK_MAX_REDRAWS):
        value = rng.gauss(0.0, sd)
        if abs(value) <= limit:
            return value
    return rng.uniform(-limit, limit)


def target_extent(
    width: float | None,
    height: float | None,
    dx: float,
    dy: float,
) -> float | None:
    """Target size along the direction of approach, for Fitts's law."""
    if not width or not height:
        return None
    distance = math.hypot(dx, dy)
    if distance < _MIN_MOVE_PX:
        return min(width, height)
    return abs(width * dx / distance) + abs(height * dy / distance)


def plan_click(rng: random.Random, profile: MouseProfile) -> ClickPlan:
    if profile.press_median_s is not None:
        pre = lognormal(
            rng, profile.press_median_s, _PRESS_FAST_SIGMA, _PRESS_CUSTOM_RANGE_S
        )
    elif rng.random() < _PRESS_FAST_P:
        pre = lognormal(
            rng, _PRESS_FAST_MEDIAN_S, _PRESS_FAST_SIGMA, _PRESS_FAST_RANGE_S
        )
    else:
        pre = lognormal(
            rng, _PRESS_SLOW_MEDIAN_S, _PRESS_SLOW_SIGMA, _PRESS_SLOW_RANGE_S
        )
    hold_range = _HOLD_RANGE_S
    lo, hi = _HOLD_MEDIAN_RANGE_S
    if not lo <= profile.hold_median_s <= hi:
        hold_range = (
            profile.hold_median_s * _HOLD_CUSTOM_FLOOR_FRAC,
            profile.hold_median_s * _HOLD_CUSTOM_CEIL_FRAC,
        )
    hold = lognormal(rng, profile.hold_median_s, _HOLD_SIGMA, hold_range)
    if rng.random() >= _WOBBLE_P:
        return ClickPlan(pre_press_s=pre, hold_s=hold, wobble=None)

    step = rng.choice(_WOBBLE_STEPS_PX)
    at = hold * rng.uniform(*_WOBBLE_AT_FRAC)
    wobble = (at, step, 0) if rng.random() < 0.5 else (at, 0, step)
    return ClickPlan(pre_press_s=pre, hold_s=hold, wobble=wobble)


def plan_post_release(rng: random.Random) -> float:
    """Pause after mouseup before the pointer does anything else."""
    return lognormal(
        rng, _POST_RELEASE_MEDIAN_S, _POST_RELEASE_SIGMA, _POST_RELEASE_RANGE_S
    )
