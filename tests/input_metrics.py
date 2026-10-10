"""Kinematic and timing metrics for humanized input checks.

Shared by the planner unit tests and the browser probe checks, so both judge
input by the same yardstick. The stroke metrics mirror the ones used to fit
the mouse model to the SapiMouse dataset.
"""

from __future__ import annotations

import math
import statistics

_MIN_DT_S = 1e-3
_MIN_STROKE_SAMPLES = 5
_PEAK_FLOOR_FRAC = 0.15
_PEAK_DIP_FRAC = 0.6
OVERSHOOT_MIN_PX = 2.0
BROWSER_FRAME_HZ = 60.0


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of empty data")
    index = (len(ordered) - 1) * q
    lo = math.floor(index)
    hi = math.ceil(index)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def pearson(xs: list[float], ys: list[float]) -> float:
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def count_peaks(speeds: list[float]) -> int:
    """Velocity peaks separated by a dip below 60% of the smaller peak."""
    if len(speeds) < 3:
        return 1
    padded = [0.0, *speeds, 0.0]
    smooth = [sum(padded[i : i + 3]) / 3.0 for i in range(len(speeds))]
    vmax = max(smooth)
    if vmax <= 0:
        return 0
    peaks: list[float] = []
    trough = math.inf
    for i in range(1, len(smooth) - 1):
        trough = min(trough, smooth[i])
        is_local_max = smooth[i] >= smooth[i - 1] and smooth[i] >= smooth[i + 1]
        if not is_local_max or smooth[i] <= _PEAK_FLOOR_FRAC * vmax:
            continue
        if not peaks or trough < _PEAK_DIP_FRAC * min(peaks[-1], smooth[i]):
            peaks.append(smooth[i])
            trough = math.inf
            continue
        peaks[-1] = max(peaks[-1], smooth[i])
    return max(1, len(peaks))


def stroke_features(points: list[tuple[float, float, float]]) -> dict | None:
    """Features of one aimed movement given (t, x, y) samples, start first."""
    if len(points) < _MIN_STROKE_SAMPLES:
        return None
    t0 = points[0][0]
    ts = [p[0] - t0 for p in points]
    xs = [p[1] for p in points]
    ys = [p[2] for p in points]
    segments = [
        math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) for i in range(len(xs) - 1)
    ]
    path = sum(segments)
    distance = math.hypot(xs[-1] - xs[0], ys[-1] - ys[0])
    duration = ts[-1]
    if distance <= 0 or duration <= 0 or path <= 0:
        return None
    speeds = [seg / max(ts[i + 1] - ts[i], _MIN_DT_S) for i, seg in enumerate(segments)]
    peak_index = max(range(len(speeds)), key=speeds.__getitem__)
    ax = (xs[-1] - xs[0]) / distance
    ay = (ys[-1] - ys[0]) / distance
    along = [(x - xs[0]) * ax + (y - ys[0]) * ay for x, y in zip(xs, ys)]
    return {
        "distance": distance,
        "duration": duration,
        "time_to_peak": (ts[peak_index] + ts[peak_index + 1]) / 2.0 / duration,
        "peak_to_mean": max(speeds) / (path / duration),
        "straightness": distance / path,
        "peaks": count_peaks(speeds),
        "overshoot": max(along) - distance > OVERSHOOT_MIN_PX,
    }


def coalesce(
    points: list[tuple[float, float, float]],
    hz: float = BROWSER_FRAME_HZ,
) -> list[tuple[float, float, float]]:
    """Keep the last sample per display frame, like browser mousemove."""
    frame = 1.0 / hz
    by_frame: dict[int, tuple[float, float, float]] = {}
    for t, x, y in points:
        by_frame[int(t / frame)] = (t, x, y)
    out: list[tuple[float, float, float]] = []
    for index in sorted(by_frame):
        _, x, y = by_frame[index]
        if out and (out[-1][1], out[-1][2]) == (x, y):
            continue
        out.append(((index + 1) * frame, x, y))
    return out


def log_stats(values: list[float]) -> tuple[float, float]:
    """Median and log-space standard deviation of positive values."""
    logs = [math.log(v) for v in values if v > 0]
    return math.exp(statistics.median(logs)), statistics.pstdev(logs)


def skewness(values: list[float]) -> float:
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if sd == 0:
        return 0.0
    return statistics.fmean([((v - mean) / sd) ** 3 for v in values])
