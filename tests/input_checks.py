"""Judge what a page observed during humanized (or robotic) input.

Usage:
    input_checks.py <mouse|typing|scroll> <harvest.json> <scores.json>
        --expect <human|robot> [--notches N]

harvest.json is window.__probe.harvest() from tests/fixtures/input_probe.html;
scores.json is the input detector container's output for the same harvest.
With --expect human every check must pass. With --expect robot the session
must be caught: enough of our checks fail and, for mouse and typing, a
detector flags it too. Prints one line per check and exits non-zero on a wrong verdict.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from input_metrics import skewness, stroke_features  # noqa: E402

_EXPECT_HUMAN = "human"
_EXPECT_ROBOT = "robot"
_MODE_MOUSE = "mouse"
_MODE_TYPING = "typing"
_MODE_SCROLL = "scroll"
# (own checks that must fail, whether a detector must also flag it). Wheel
# cadence is invisible to the detectors, so scroll relies on our checks.
_ROBOT_RULES = {
    _MODE_MOUSE: (2, True),
    _MODE_TYPING: (2, True),
    _MODE_SCROLL: (1, False),
}
_MS_PER_S = 1000.0
_STROKE_IDLE_GAP_S = 0.3

_TARGET_PREFIX = "target-"
# Planned holds are 45-260 ms; browser event timestamps add a few ms of jitter.
_CLICK_HOLD_RANGE_MS = (30.0, 300.0)
_CLICK_HOLD_MEDIAN_RANGE_MS = (65.0, 160.0)
_APPROACH_MIN_MOVES = 5
_CENTRE_TOLERANCE_PX = 1.0
_MIN_STROKES = 8
_TIME_TO_PEAK_RANGE = (0.1, 0.5)
_STRAIGHTNESS_RANGE = (0.6, 0.995)
_PERFECTLY_STRAIGHT = 0.995
_PERFECTLY_STRAIGHT_MAX_SHARE = 0.5
_MULTI_PEAK_MIN_SHARE = 0.2
# Humans overshoot on ~35% of aimed moves; at 30 strokes a 5% floor keeps
# false failures near 0.1% while a straight-line mover scores 0.
_OVERSHOOT_MIN_SHARE = 0.05
_MOVE_GAP_MEDIAN_RANGE_MS = (4.0, 30.0)
_GAITCHA_PASS_SCORE = 0.5
_GAITCHA_MIN_PASS_SHARE = 0.75

_KEY_HOLD_MIN_MS = 25.0
_KEY_HOLD_MEDIAN_RANGE_MS = (60.0, 170.0)
_KEY_GAP_MIN_CV = 0.3
_SHIFT_LEAD_MIN_MS = 20.0
_SHIFT_CODES = frozenset({"ShiftLeft", "ShiftRight"})

_WHEEL_MIN_GAP_MS = 10.0

# Only mouse sessions get an overall verdict. Typing and scroll sessions have
# almost no pointer data, so their overall score swings on "not enough
# evidence" mouse penalties (0.45 to 0.86 for the same typing test). Typing is
# judged on motion-attestation's keystroke channel instead.
_VERDICT_MODES = frozenset({_MODE_MOUSE})
_MA_KEYSTROKES = "keystrokes"
_MA_CHANNEL_MAX_SHARE = 0.5

# motion-attestation reasons that only machine input produces. Its softer
# heuristics also fire on ordinary human sessions, so they are not gated on:
# "no scroll direction reversals" (most readers never scroll back) and
# "periodic movement", which fired on all of 80 real 520-point SapiMouse
# sessions (median correlation 0.90) because any smooth movement is
# self-similar five samples apart.
_MA_ROBOT_REASONS = {
    _MODE_MOUSE: (
        "Perfectly straight paths",
        "Constant velocity",
        "Mouse teleportation",
        "No micro-tremor",
        "Impossibly fast clicks",
        "zero-duration clicks",
        "Zero-time mousedown/mouseup",
        "Perfectly uniform click duration",
        "Click offset variance too low",
    ),
    _MODE_TYPING: (
        "Key dwell impossibly short",
        "Uniform key dwell",
        "All key dwells identical",
        "impossibly fast key transitions",
        "Uniform flight time",
    ),
    _MODE_SCROLL: (
        "Constant scroll velocity",
        "No scroll pauses",
    ),
}
_MA_NO_SCROLL_PAUSES = "No scroll pauses"

Check = tuple[str, bool, str]


def _trusted(events: list[dict]) -> Check:
    untrusted = sum(1 for e in events if not e.get("trusted"))
    return ("all events trusted", untrusted == 0, f"untrusted={untrusted}")


def _in_range(value: float, bounds: tuple[float, float]) -> bool:
    return bounds[0] <= value <= bounds[1]


def _target_clicks(events: list[dict]) -> list[tuple[dict, dict, dict]]:
    """(mousedown, mouseup, click) triples on probe targets."""
    triples = []
    down = up = None
    for e in events:
        if e["type"] == "mousedown":
            down, up = e, None
        elif e["type"] == "mouseup":
            up = e
        elif e["type"] == "click" and (e.get("id") or "").startswith(_TARGET_PREFIX):
            if down is not None and up is not None:
                triples.append((down, up, e))
            down = up = None
    return triples


def _strokes(events: list[dict]) -> list[dict]:
    """Movement features for each approach to a mousedown.

    An approach is the last run of moves before the press, after the
    previous release and after any idle gap, as in the human-data analysis.
    """
    features = []
    segment: list[tuple[float, float, float]] = []
    for e in events:
        if e["type"] == "mouseup":
            segment = []
            continue
        if e["type"] == "mousemove":
            point = (e["t"] / _MS_PER_S, e["x"], e["y"])
            if segment and point[0] - segment[-1][0] > _STROKE_IDLE_GAP_S:
                segment = []
            segment.append(point)
            continue
        if e["type"] != "mousedown":
            continue
        feature = stroke_features(segment) if segment else None
        if feature:
            features.append(feature)
        segment = []
    return features


def mouse_checks(harvest: dict) -> list[Check]:
    events = harvest["events"]
    checks = [_trusted(events)]
    clicks = _target_clicks(events)
    checks.append(
        ("clicks recorded", len(clicks) >= _MIN_STROKES, f"clicks={len(clicks)}")
    )
    if not clicks:
        return checks

    holds = [up["t"] - down["t"] for down, up, _ in clicks]
    checks.append(
        (
            "click hold in human range",
            all(_in_range(h, _CLICK_HOLD_RANGE_MS) for h in holds)
            and _in_range(statistics.median(holds), _CLICK_HOLD_MEDIAN_RANGE_MS),
            f"min={min(holds):.1f}ms median={statistics.median(holds):.1f}ms max={max(holds):.1f}ms",
        )
    )

    moves = [e for e in events if e["type"] == "mousemove"]
    starved = 0
    previous_release = -1.0
    for down, up, _ in clicks:
        approach = [m for m in moves if previous_release < m["t"] < down["t"]]
        starved += len(approach) < _APPROACH_MIN_MOVES
        previous_release = up["t"]
    checks.append(
        (
            "pointer moves before every click",
            starved == 0,
            f"clicks_without_approach={starved}",
        )
    )

    centred = inside = 0
    for _, _, click in clicks:
        rect = click["rect"]
        cx = rect["l"] + rect["w"] / 2.0
        cy = rect["t"] + rect["h"] / 2.0
        is_centred = (
            abs(click["x"] - cx) < _CENTRE_TOLERANCE_PX
            and abs(click["y"] - cy) < _CENTRE_TOLERANCE_PX
        )
        centred += is_centred
        inside += (
            rect["l"] <= click["x"] <= rect["l"] + rect["w"]
            and rect["t"] <= click["y"] <= rect["t"] + rect["h"]
        )
    checks.append(("no dead-centre clicks", centred == 0, f"dead_centre={centred}"))
    checks.append(
        (
            "clicks land on target",
            inside == len(clicks),
            f"inside={inside}/{len(clicks)}",
        )
    )

    gaps = [b["t"] - a["t"] for a, b in zip(moves, moves[1:]) if b["t"] > a["t"]]
    if gaps:
        checks.append(
            (
                "mousemove cadence",
                _in_range(statistics.median(gaps), _MOVE_GAP_MEDIAN_RANGE_MS),
                f"median_gap={statistics.median(gaps):.1f}ms",
            )
        )

    strokes = _strokes(events)
    checks.append(
        ("approach strokes", len(strokes) >= _MIN_STROKES, f"strokes={len(strokes)}")
    )
    if len(strokes) < _MIN_STROKES:
        return checks
    time_to_peak = statistics.median(s["time_to_peak"] for s in strokes)
    straightness = statistics.median(s["straightness"] for s in strokes)
    perfectly_straight = sum(
        s["straightness"] > _PERFECTLY_STRAIGHT for s in strokes
    ) / len(strokes)
    multi_peak = sum(s["peaks"] >= 2 for s in strokes) / len(strokes)
    overshoot = sum(s["overshoot"] for s in strokes) / len(strokes)
    checks += [
        (
            "bell-shaped speed profile",
            _in_range(time_to_peak, _TIME_TO_PEAK_RANGE),
            f"time_to_peak={time_to_peak:.3f}",
        ),
        (
            "curved paths",
            _in_range(straightness, _STRAIGHTNESS_RANGE),
            f"straightness={straightness:.3f}",
        ),
        (
            "few perfectly straight paths",
            perfectly_straight <= _PERFECTLY_STRAIGHT_MAX_SHARE,
            f"share={perfectly_straight:.2f}",
        ),
        (
            "corrective submovements",
            multi_peak >= _MULTI_PEAK_MIN_SHARE,
            f"multi_peak_share={multi_peak:.2f}",
        ),
        (
            "overshoot and correct",
            overshoot >= _OVERSHOOT_MIN_SHARE,
            f"overshoot_share={overshoot:.2f}",
        ),
    ]
    return checks


def typing_checks(harvest: dict) -> list[Check]:
    events = [e for e in harvest["events"] if e["type"] in ("keydown", "keyup")]
    checks = [_trusted(events)]
    repeats = sum(1 for e in events if e.get("rep"))
    checks.append(("no auto-repeat", repeats == 0, f"repeats={repeats}"))

    open_downs: dict[str, float] = {}
    holds: list[float] = []
    downs: list[dict] = []
    unmatched = 0
    for e in events:
        code = e.get("code") or e.get("key")
        if e["type"] == "keydown":
            downs.append(e)
            open_downs[code] = e["t"]
            continue
        if code not in open_downs:
            unmatched += 1
            continue
        holds.append(e["t"] - open_downs.pop(code))
    checks.append(
        (
            "every key released",
            unmatched == 0 and not open_downs,
            f"unmatched={unmatched} stuck={len(open_downs)}",
        )
    )
    if not holds:
        checks.append(("key holds recorded", False, "holds=0"))
        return checks
    checks.append(
        (
            "key holds in human range",
            min(holds) >= _KEY_HOLD_MIN_MS
            and _in_range(statistics.median(holds), _KEY_HOLD_MEDIAN_RANGE_MS),
            f"min={min(holds):.1f}ms median={statistics.median(holds):.1f}ms",
        )
    )

    presses = [e for e in downs if e.get("code") not in _SHIFT_CODES]
    gaps = [b["t"] - a["t"] for a, b in zip(presses, presses[1:])]
    if len(gaps) >= 3:
        cv = statistics.pstdev(gaps) / statistics.fmean(gaps)
        checks.append(
            (
                "irregular key rhythm",
                cv >= _KEY_GAP_MIN_CV and skewness(gaps) > 0,
                f"cv={cv:.2f} skew={skewness(gaps):.2f}",
            )
        )

    late_shift = 0
    capitals = 0
    shift_down_at: float | None = None
    is_first_capital = False
    for e in events:
        if e.get("code") in _SHIFT_CODES:
            is_down = e["type"] == "keydown"
            shift_down_at = e["t"] if is_down else None
            is_first_capital = is_down
            continue
        key = e.get("key") or ""
        if e["type"] != "keydown" or len(key) != 1 or not key.isupper():
            continue
        capitals += 1
        if shift_down_at is None:
            late_shift += 1
            continue
        # Later capitals in a run share the Shift already held.
        if is_first_capital and e["t"] - shift_down_at < _SHIFT_LEAD_MIN_MS:
            late_shift += 1
        is_first_capital = False
    checks.append(
        (
            "shift pressed well before capitals",
            capitals > 0 and late_shift == 0,
            f"capitals={capitals} late={late_shift}",
        )
    )
    return checks


def scroll_checks(harvest: dict, notches: int | None) -> list[Check]:
    wheels = [e for e in harvest["events"] if e["type"] == "wheel"]
    checks = [_trusted(wheels)]
    if notches is not None:
        checks.append(
            (
                "one wheel event per notch",
                len(wheels) == notches,
                f"wheel_events={len(wheels)} notches={notches}",
            )
        )
    if not wheels:
        return checks
    deltas = {abs(e["dY"]) for e in wheels}
    legacy = {abs(e["wY"]) for e in wheels if e.get("wY") is not None}
    checks.append(
        (
            "every wheel event is a single notch",
            len(deltas) == 1 and len(legacy) <= 1,
            f"deltaY={sorted(deltas)} wheelDeltaY={sorted(legacy)}",
        )
    )
    gaps = [b["t"] - a["t"] for a, b in zip(wheels, wheels[1:])]
    if gaps:
        checks.append(
            (
                "notches spaced out",
                min(gaps) >= _WHEEL_MIN_GAP_MS,
                f"min_gap={min(gaps):.1f}ms",
            )
        )
    return checks


def detector_checks(mode: str, scores: dict, is_single_gesture: bool) -> list[Check]:
    checks = []
    ma = scores.get("motion_attestation")
    if ma is not None:
        patterns = _MA_ROBOT_REASONS[mode]
        if is_single_gesture:
            # One flick has no pause by nature; only page-long scrolling does.
            patterns = tuple(p for p in patterns if p != _MA_NO_SCROLL_PAUSES)
        robot_reasons = [r for r in ma["reasons"] if any(p in r for p in patterns)]
        if mode == _MODE_TYPING:
            channel = ma["categories"][_MA_KEYSTROKES]
            checks.append(
                (
                    "motion-attestation: keystroke channel clean",
                    channel["penalty"] <= channel["maxPenalty"] * _MA_CHANNEL_MAX_SHARE,
                    f"penalty={channel['penalty']} max={channel['maxPenalty']} reasons={channel['reasons']}",
                )
            )
        if mode in _VERDICT_MODES:
            checks.append(
                (
                    "motion-attestation: human verdict",
                    ma["verdict"] == _EXPECT_HUMAN,
                    f"score={ma['score']} verdict={ma['verdict']}",
                )
            )
        checks.append(
            (
                "motion-attestation: no machine-only reasons",
                not robot_reasons,
                "; ".join(robot_reasons) or "none",
            )
        )
    gaitcha = scores.get("gaitcha") or []
    if mode == _MODE_MOUSE and gaitcha:
        passed = sum(1 for g in gaitcha if g["score"] >= _GAITCHA_PASS_SCORE)
        checks.append(
            (
                "gaitcha: clicks pass",
                passed / len(gaitcha) >= _GAITCHA_MIN_PASS_SHARE,
                f"passed={passed}/{len(gaitcha)} scores={[round(g['score'], 2) for g in gaitcha]}",
            )
        )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=(_MODE_MOUSE, _MODE_TYPING, _MODE_SCROLL))
    parser.add_argument("harvest", type=Path)
    parser.add_argument("scores", type=Path)
    parser.add_argument(
        "--expect", choices=(_EXPECT_HUMAN, _EXPECT_ROBOT), required=True
    )
    # A single scroll request of N notches (rather than a page-long session).
    parser.add_argument("--notches", type=int)
    args = parser.parse_args()

    harvest = json.loads(args.harvest.read_text())
    scores = json.loads(args.scores.read_text())
    builders: dict[str, Callable[[], list[Check]]] = {
        _MODE_MOUSE: lambda: mouse_checks(harvest),
        _MODE_TYPING: lambda: typing_checks(harvest),
        _MODE_SCROLL: lambda: scroll_checks(harvest, args.notches),
    }
    own = builders[args.mode]()
    detectors = detector_checks(args.mode, scores, args.notches is not None)
    for name, ok, detail in own + detectors:
        print(f"  {'pass' if ok else 'FAIL'}: {name} ({detail})")

    if args.expect == _EXPECT_HUMAN:
        return 0 if all(ok for _, ok, _ in own + detectors) else 1

    min_failed, needs_detector = _ROBOT_RULES[args.mode]
    own_failed = sum(1 for _, ok, _ in own if not ok)
    detector_flagged = any(not ok for _, ok, _ in detectors)
    is_caught = own_failed >= min_failed and (detector_flagged or not needs_detector)
    print(
        f"  robot control: own_checks_failed={own_failed} detector_flagged={detector_flagged}"
    )
    return 0 if is_caught else 1


if __name__ == "__main__":
    sys.exit(main())
