"""Human-like mouse wheel timing.

People scroll in gestures: a burst of wheel notches, then a pause to read.
Within a gesture the notch rhythm is either slow and controlled or a fast
flick that speeds up and slows down at the ends (Hinckley et al. 2002).
Gesture sizes and pauses follow the Balabit dataset and reading-speed
research.

Notches are never sent closer together than a minimum gap. Back-to-back
notches arrive with a 0 ms cadence no physical wheel produces, and some
browsers merge them into one wheel event covering several notches.
"""

from __future__ import annotations

import random

from human_mouse import lognormal

MIN_NOTCH_GAP_S = 0.015

_SINGLE_NOTCH_P = 0.25
_GESTURE_MEDIAN_NOTCHES = 3.5
_GESTURE_SIGMA = 0.6
_GESTURE_RANGE = (2, 18)
_SHORT_GESTURE_MAX = 4

_CONTROLLED_P_SHORT = 0.6
_CONTROLLED_P_LONG = 0.2
_CONTROLLED_MEDIAN_S = 0.12
_CONTROLLED_SIGMA = 0.35
_CONTROLLED_RANGE_S = (0.07, 0.3)
_FLICK_MEDIAN_S = 0.035
_FLICK_SIGMA = 0.35
_FLICK_RANGE_S = (MIN_NOTCH_GAP_S, 0.09)
# A flick ramps up and down: the first and last gaps are slower.
_FLICK_RAMP_RANGE = (1.5, 2.0)
_FLICK_RAMP_GAPS = 2
_CUSTOM_GAP_FLOOR_FRAC = 0.5
_CUSTOM_GAP_CEIL_FRAC = 3.0

# Pause between the gestures of one scroll request.
_BETWEEN_GESTURES_MEDIAN_S = 0.35
_BETWEEN_GESTURES_SIGMA = 0.5
_BETWEEN_GESTURES_RANGE_S = (0.15, 1.5)

# Reading pause between gestures while scrolling through a page, as
# multiples of the caller's median delay.
_READING_SIGMA = 0.6
_READING_RANGE_FRAC = (0.4, 6.0)

SCROLL_BACK_P = 0.06
SCROLL_BACK_MAX_PER_PAGE = 2
_SCROLL_BACK_NOTCHES = (1, 4)
_SCROLL_BACK_PAUSE_FRAC = (1.0, 4.0)


def gesture_size(
    rng: random.Random,
    remaining: int | None = None,
    bounds: tuple[int, int] | None = None,
) -> int:
    """Notches in one gesture, capped by what is left to scroll."""
    if bounds is not None:
        lo, hi = bounds
        median = min(max(_GESTURE_MEDIAN_NOTCHES, lo), hi)
        size = round(lognormal(rng, median, _GESTURE_SIGMA, (lo, hi)))
    elif rng.random() < _SINGLE_NOTCH_P:
        size = 1
    else:
        size = round(
            lognormal(rng, _GESTURE_MEDIAN_NOTCHES, _GESTURE_SIGMA, _GESTURE_RANGE)
        )
    if remaining is not None:
        size = min(size, remaining)
    return max(size, 1)


def plan_gesture(
    notches: int,
    rng: random.Random,
    notch_gap_s: float | None = None,
) -> list[float]:
    """Gaps in seconds before each notch of one gesture; the first is 0.

    notch_gap_s, when given, replaces the flick/controlled rhythm with
    gaps spread around that median.
    """
    if notches <= 0:
        return []
    if notch_gap_s is not None:
        median = max(notch_gap_s, MIN_NOTCH_GAP_S)
        bounds = (
            max(MIN_NOTCH_GAP_S, median * _CUSTOM_GAP_FLOOR_FRAC),
            median * _CUSTOM_GAP_CEIL_FRAC,
        )
        gaps = [
            lognormal(rng, median, _CONTROLLED_SIGMA, bounds)
            for _ in range(notches - 1)
        ]
        return [0.0, *gaps]
    is_short = notches <= _SHORT_GESTURE_MAX
    controlled_p = _CONTROLLED_P_SHORT if is_short else _CONTROLLED_P_LONG
    if rng.random() < controlled_p:
        gaps = [
            lognormal(rng, _CONTROLLED_MEDIAN_S, _CONTROLLED_SIGMA, _CONTROLLED_RANGE_S)
            for _ in range(notches - 1)
        ]
        return [0.0, *gaps]

    gaps = [
        lognormal(rng, _FLICK_MEDIAN_S, _FLICK_SIGMA, _FLICK_RANGE_S)
        for _ in range(notches - 1)
    ]
    ramp = min(_FLICK_RAMP_GAPS, len(gaps) // 2)
    for i in range(ramp):
        gaps[i] *= rng.uniform(*_FLICK_RAMP_RANGE)
        gaps[-1 - i] *= rng.uniform(*_FLICK_RAMP_RANGE)
    return [0.0, *(max(g, MIN_NOTCH_GAP_S) for g in gaps)]


def plan_scroll(
    notches: int,
    rng: random.Random,
    notch_gap_s: float | None = None,
) -> list[float]:
    """Gaps before each notch for a scroll of abs(notches) notches.

    Small amounts are one gesture; larger ones are split into gestures
    separated by short pauses.
    """
    remaining = abs(notches)
    if remaining <= _SHORT_GESTURE_MAX:
        return plan_gesture(remaining, rng, notch_gap_s)
    gaps: list[float] = []
    while remaining > 0:
        size = gesture_size(rng, remaining)
        gesture = plan_gesture(size, rng, notch_gap_s)
        if gaps:
            gesture[0] = lognormal(
                rng,
                _BETWEEN_GESTURES_MEDIAN_S,
                _BETWEEN_GESTURES_SIGMA,
                _BETWEEN_GESTURES_RANGE_S,
            )
        gaps.extend(gesture)
        remaining -= size
    return gaps


def reading_pause(rng: random.Random, median_s: float) -> float:
    lo, hi = _READING_RANGE_FRAC
    return lognormal(rng, median_s, _READING_SIGMA, (median_s * lo, median_s * hi))


def scroll_back_notches(rng: random.Random) -> int:
    return rng.randint(*_SCROLL_BACK_NOTCHES)


def scroll_back_pause(rng: random.Random, median_s: float) -> float:
    return median_s * rng.uniform(*_SCROLL_BACK_PAUSE_FRAC)
