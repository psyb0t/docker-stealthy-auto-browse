"""Human-like keystroke timing.

plan_typing turns text into a timeline of key down and key up events. The
model follows the Aalto 136M keystrokes study (Dhakal et al., CHI 2018) and
the CMU keystroke benchmark:

- gaps between key presses are a shifted lognormal around the typist's
  median, scaled by the key pair (alternating hands are fast, the same finger
  is slow, starting a word is slow) and by a slow pace drift;
- every key has its own hold time, so fast typists press the next key before
  releasing the previous one, and the same key never overlaps itself;
- Shift is pressed with the opposite hand well before the letter and held
  across runs of capitals;
- optional typos (adjacent key, transposition, extra or missed letter) are
  noticed right away or a few keys later, erased with Backspace and
  retyped, so the final text is always exactly the requested text.

The planner is pure and seedable. System replays the events with XTest.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

KEY_SHIFT_LEFT = "shiftleft"
KEY_SHIFT_RIGHT = "shiftright"
KEY_BACKSPACE = "backspace"
KEY_SPACE = "space"
KEY_ENTER = "enter"
KEY_TAB = "tab"
SHIFT_KEYS = frozenset({KEY_SHIFT_LEFT, KEY_SHIFT_RIGHT})

# US layout: shifted symbol -> base key.
_SHIFTED_SYMBOLS = {
    "~": "`",
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
}
_SPECIAL_KEYS = {" ": KEY_SPACE, "\n": KEY_ENTER, "\t": KEY_TAB}
_SPECIAL_CHARS = {key: char for char, key in _SPECIAL_KEYS.items()}
_UNSHIFTED_TO_SYMBOL = {base: symbol for symbol, base in _SHIFTED_SYMBOLS.items()}
_BASE_KEYS = frozenset("`1234567890-=qwertyuiop[]\\asdfghjkl;'zxcvbnm,./")

# Touch-typing finger per base key: (hand, finger), hand 0 = left.
_FINGER_ROWS = (
    ((0, 0), "`1qaz"),
    ((0, 1), "2wsx"),
    ((0, 2), "3edc"),
    ((0, 3), "45rtfgvb"),
    ((1, 3), "67yuhjnm"),
    ((1, 2), "8ik,"),
    ((1, 1), "9ol."),
    ((1, 0), "0-=p[]\\;'/"),
)
_FINGERS = {key: finger for finger, keys in _FINGER_ROWS for key in keys}
_LEFT_HAND = 0

_QWERTY_ROWS = ("1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./")

# Very frequent English letter pairs are typed as one practised movement.
_FREQUENT_BIGRAMS = frozenset(
    (
        "th he in er an re on at en nd ti es or te of ed is it al ar st to "
        "nt ng se ha as ou io le ve co me de hi ri ro ic ne ea ra ce li ch "
        "ll be ma si om ur"
    ).split()
)

# Typist tiers from the Aalto per-user medians:
# (weight, median gap s, gap log-sigma, rollover keep probability).
_TIERS = (
    (0.10, 0.245, 0.66, 0.25),
    (0.50, 0.161, 0.57, 0.7),
    (0.30, 0.119, 0.52, 1.0),
    (0.10, 0.096, 0.48, 1.0),
)
_TIER_MEDIAN_LOG_SIGMA = 0.12
_HOLD_MEDIAN_MU_S = 0.104
_HOLD_MEDIAN_SD_S = 0.015
_HOLD_MEDIAN_RANGE_S = (0.075, 0.145)
_HOLD_SIGMA = 0.33
_HOLD_RANGE_S = (0.035, 0.3)
_BACKSPACE_HOLD_FRAC = 0.77
_HOLD_FLOOR_FRAC = 0.4
_HOLD_CEIL_FRAC = 2.5

_GAP_SHIFT_S = 0.015
_GAP_FLOOR_S = 0.045
_GAP_MEDIAN_FLOOR_S = 0.001
_SAME_KEY_RELEASE_GAP_S = 0.02
# A key typed twice is held at least this long the first time, so a double
# letter is never faster than a finger can lift and press again.
_SAME_KEY_MIN_HOLD_S = 0.06
_EARLY_RELEASE_RANGE_S = (0.005, 0.03)
_MIN_HOLD_S = 0.035
# Keys pressed this far back are always released already (hold <= 0.3 s,
# gap >= 45 ms).
_UNICODE_LOOKBACK = 8

# Key-pair gap multipliers (Aalto sample, n-weighted).
_MULT_ALTERNATING_HAND = 0.88
_MULT_SAME_HAND = 1.0
_MULT_SAME_FINGER = 1.26
_MULT_SAME_KEY = 0.98
_MULT_FREQUENT_BIGRAM = 0.75
_MULT_TO_SPACE = 0.84
_MULT_WORD_START = 1.29
_MULT_TO_PUNCTUATION = 1.8
_MULT_TO_DIGIT = 2.0
_MULT_FIRST_SHIFTED = 2.2

_SENTENCE_PAUSE_P = 0.3
_SENTENCE_PAUSE_RANGE_S = (0.3, 1.2)
_COMMA_PAUSE_P = 0.2
_COMMA_PAUSE_RANGE_S = (0.1, 0.35)
_SENTENCE_ENDS = frozenset(".!?")

# Pace drift: Ornstein-Uhlenbeck process on the log of the gap multiplier.
_DRIFT_REVERSION = 0.02
_DRIFT_VOLATILITY = 0.045
_DRIFT_RANGE = (0.8, 1.3)

# Shift is pressed ~159 ms before the letter (Aalto) and released ~22 ms
# before it, sometimes after.
_SHIFT_LEAD_MEDIAN_S = 0.159
_SHIFT_LEAD_SIGMA = 0.5
_SHIFT_LEAD_MIN_S = 0.03
_SHIFT_LEAD_MAX_FRAC = 0.8
_SHIFT_RELEASE_MU_S = -0.022
_SHIFT_RELEASE_SD_S = 0.04
_SHIFT_RELEASE_MIN_AFTER_DOWN_S = 0.015
_SHIFT_RELEASE_BEFORE_NEXT_S = 0.005

# Typos: Dhakal's error mix plus transpositions; correction timing in
# multiples of the typist's median gap.
DEFAULT_TYPO_RATE = 0.02
_TYPO_SUBSTITUTE = "substitute"
_TYPO_TRANSPOSE = "transpose"
_TYPO_INSERT = "insert"
_TYPO_OMIT = "omit"
_TYPO_KINDS = (
    (_TYPO_SUBSTITUTE, 0.5),
    (_TYPO_TRANSPOSE, 0.15),
    (_TYPO_INSERT, 0.15),
    (_TYPO_OMIT, 0.2),
)
_NOTICE_AFTER = ((0, 0.55), (1, 0.25), (2, 0.1), (3, 0.05))
_NOTICE_PAUSE_MULT = 2.65
_NOTICE_PAUSE_SIGMA = 0.4
_BACKSPACE_GAP_MULT = 0.95
_RESUME_PAUSE_MULT = 1.44
_DOUBLED_INSERT_P = 0.4


@dataclass(frozen=True)
class TypingProfile:
    """Per-session typist traits."""

    median_gap_s: float
    gap_sigma: float
    hold_median_s: float
    rollover_keep_p: float

    @classmethod
    def sample(cls, rng: random.Random) -> TypingProfile:
        tier = rng.choices(_TIERS, weights=[t[0] for t in _TIERS])[0]
        _, median, sigma, keep = tier
        hold = rng.gauss(_HOLD_MEDIAN_MU_S, _HOLD_MEDIAN_SD_S)
        return cls(
            median_gap_s=median * math.exp(rng.gauss(0.0, _TIER_MEDIAN_LOG_SIGMA)),
            gap_sigma=sigma,
            hold_median_s=min(
                max(hold, _HOLD_MEDIAN_RANGE_S[0]), _HOLD_MEDIAN_RANGE_S[1]
            ),
            rollover_keep_p=keep,
        )

    def with_median_gap(self, median_gap_s: float) -> TypingProfile:
        """Same typist at a caller-chosen speed, with that tier's spread."""
        tier = min(_TIERS, key=lambda t: abs(math.log(t[1] / median_gap_s)))
        return TypingProfile(
            median_gap_s=median_gap_s,
            gap_sigma=tier[2],
            hold_median_s=self.hold_median_s,
            rollover_keep_p=tier[3],
        )


@dataclass(frozen=True)
class KeyEvent:
    """One key transition. key is a pyautogui key name, or the character
    itself when is_unicode (no key on the US layout)."""

    t: float
    is_down: bool
    key: str
    is_unicode: bool = False


@dataclass(frozen=True)
class _Keystroke:
    char: str
    key: str
    needs_shift: bool
    is_unicode: bool
    # Correction timing overrides; None = key-pair model and typist spread.
    gap_mult: float | None = None
    gap_sigma: float | None = None


def _keystroke(char: str, gap_mult: float | None = None) -> _Keystroke:
    if char in _SPECIAL_KEYS:
        return _Keystroke(char, _SPECIAL_KEYS[char], False, False, gap_mult)
    if char in _BASE_KEYS:
        return _Keystroke(char, char, False, False, gap_mult)
    lower = char.lower()
    if char.isupper() and lower in _BASE_KEYS:
        return _Keystroke(char, lower, True, False, gap_mult)
    if char in _SHIFTED_SYMBOLS:
        return _Keystroke(char, _SHIFTED_SYMBOLS[char], True, False, gap_mult)
    return _Keystroke(char, char, False, True, gap_mult)


def _backspace(is_first: bool) -> _Keystroke:
    if is_first:
        return _Keystroke(
            "\b",
            KEY_BACKSPACE,
            False,
            False,
            _NOTICE_PAUSE_MULT,
            _NOTICE_PAUSE_SIGMA,
        )
    return _Keystroke("\b", KEY_BACKSPACE, False, False, _BACKSPACE_GAP_MULT)


def is_typo_eligible(char: str) -> bool:
    return char.isascii() and char.isalpha()


def _adjacent_keys(key: str) -> list[str]:
    for row_index, row in enumerate(_QWERTY_ROWS):
        col = row.find(key)
        if col < 0:
            continue
        neighbours = [row[c] for c in (col - 1, col + 1) if 0 <= c < len(row)]
        for other in (row_index - 1, row_index + 1):
            if not 0 <= other < len(_QWERTY_ROWS):
                continue
            other_row = _QWERTY_ROWS[other]
            neighbours += [
                other_row[c] for c in (col, col + 1) if 0 <= c < len(other_row)
            ]
        return [k for k in neighbours if k.isalpha()]
    return []


def _matching_case(template: str, letter: str) -> str:
    return letter.upper() if template.isupper() else letter


def _notice_after(rng: random.Random, available: int) -> int:
    roll = rng.random()
    cumulative = 0.0
    for chars, p in _NOTICE_AFTER:
        cumulative += p
        if roll < cumulative:
            return min(chars, available)
    # Otherwise the typo is noticed at the end of the word.
    return available


def _word_tail(text: str, start: int) -> int:
    """Typo-safe characters after start, up to the end of the word."""
    count = 0
    for char in text[start:]:
        if char.isspace() or not char.isascii():
            break
        count += 1
    return count


def _typo_keystrokes(
    text: str,
    i: int,
    rng: random.Random,
) -> tuple[list[_Keystroke], int] | None:
    """Keystrokes for a typo at text[i] and its correction.

    Returns (keystrokes, characters of text consumed), or None when the
    chosen typo does not fit here.
    """
    char = text[i]
    kind = rng.choices(
        [k for k, _ in _TYPO_KINDS], weights=[w for _, w in _TYPO_KINDS]
    )[0]
    tail = _word_tail(text, i + 1)
    is_transpose = kind == _TYPO_TRANSPOSE
    if is_transpose and (tail < 1 or not is_typo_eligible(text[i + 1])):
        return None
    if kind == _TYPO_OMIT and tail < 1:
        return None

    swapped = 1 if is_transpose else 0
    after = _notice_after(rng, tail - swapped)
    if kind == _TYPO_OMIT:
        after = max(after, 1)
    follow = text[i + 1 + swapped : i + 1 + swapped + after]
    neighbours = _adjacent_keys(char.lower())

    # typed is what goes in by mistake, erase how many Backspaces undo it,
    # and retype what is typed again after the correction.
    if kind == _TYPO_SUBSTITUTE:
        if not neighbours:
            return None
        typed = _matching_case(char, rng.choice(neighbours)) + follow
        erase = len(typed)
        retype = char + follow
    elif kind == _TYPO_INSERT:
        is_doubled = not neighbours or rng.random() < _DOUBLED_INSERT_P
        extra = char if is_doubled else _matching_case(char, rng.choice(neighbours))
        typed = char + extra + follow
        erase = len(typed) - 1
        retype = follow
    elif kind == _TYPO_OMIT:
        typed = follow
        erase = len(typed)
        retype = char + follow
    else:
        typed = text[i + 1] + char + follow
        erase = len(typed)
        retype = char + text[i + 1] + follow

    strokes = [_keystroke(c) for c in typed]
    strokes += [_backspace(n == 0) for n in range(erase)]
    for n, c in enumerate(retype):
        strokes.append(_keystroke(c, _RESUME_PAUSE_MULT if n == 0 else None))
    return strokes, 1 + swapped + after


def _keystrokes(
    text: str,
    rng: random.Random,
    typo_rate: float,
) -> list[_Keystroke]:
    strokes: list[_Keystroke] = []
    i = 0
    while i < len(text):
        char = text[i]
        wants_typo = (
            typo_rate > 0 and is_typo_eligible(char) and rng.random() < typo_rate
        )
        typo = _typo_keystrokes(text, i, rng) if wants_typo else None
        if typo is None:
            strokes.append(_keystroke(char))
            i += 1
            continue
        typo_strokes, consumed = typo
        strokes.extend(typo_strokes)
        i += consumed
    return strokes


def _pair_multiplier(prev: _Keystroke | None, cur: _Keystroke) -> float:
    if prev is None:
        return 1.0
    if cur.key == KEY_SPACE:
        return _MULT_TO_SPACE
    if prev.key == KEY_SPACE:
        return _MULT_WORD_START
    if cur.char.isdigit():
        return 1.0 if prev.char.isdigit() else _MULT_TO_DIGIT
    if not cur.char.isalnum():
        return _MULT_TO_PUNCTUATION
    if (prev.char + cur.char).lower() in _FREQUENT_BIGRAMS:
        return _MULT_FREQUENT_BIGRAM
    if prev.key == cur.key:
        return _MULT_SAME_KEY
    prev_finger = _FINGERS.get(prev.key)
    cur_finger = _FINGERS.get(cur.key)
    if prev_finger is None or cur_finger is None:
        return _MULT_SAME_HAND
    if prev_finger == cur_finger:
        return _MULT_SAME_FINGER
    if prev_finger[0] == cur_finger[0]:
        return _MULT_SAME_HAND
    return _MULT_ALTERNATING_HAND


def _context_pause(text_before: str, rng: random.Random) -> float:
    """Extra thinking pause at the start of a sentence or after a comma."""
    if len(text_before) < 2 or text_before[-1] != " ":
        return 0.0
    if text_before[-2] in _SENTENCE_ENDS and rng.random() < _SENTENCE_PAUSE_P:
        return rng.uniform(*_SENTENCE_PAUSE_RANGE_S)
    if text_before[-2] == "," and rng.random() < _COMMA_PAUSE_P:
        return rng.uniform(*_COMMA_PAUSE_RANGE_S)
    return 0.0


def _shift_key_for(stroke: _Keystroke) -> str:
    finger = _FINGERS.get(stroke.key)
    if finger is not None and finger[0] == _LEFT_HAND:
        return KEY_SHIFT_RIGHT
    return KEY_SHIFT_LEFT


def plan_typing(
    text: str,
    rng: random.Random,
    profile: TypingProfile,
    typo_rate: float = 0.0,
) -> list[KeyEvent]:
    """Plan key events that type text, in time order from t = 0.

    With typo_rate > 0, ASCII letters get occasional typos that are always
    corrected, so replaying the events still produces exactly text.
    """
    strokes = _keystrokes(text, rng, typo_rate)
    # A caller-chosen hold median widens the clip range around it.
    hold_floor = min(_HOLD_RANGE_S[0], profile.hold_median_s * _HOLD_FLOOR_FRAC)
    hold_ceil = max(_HOLD_RANGE_S[1], profile.hold_median_s * _HOLD_CEIL_FRAC)
    downs: list[float] = []
    ups: list[float] = []
    last_index_by_key: dict[str, int] = {}
    shift_spans: list[tuple[str, int, int]] = []
    drift = 0.0
    typed = ""
    t = 0.0
    prev: _Keystroke | None = None
    for index, stroke in enumerate(strokes):
        drift = (1.0 - _DRIFT_REVERSION) * drift + rng.gauss(0.0, _DRIFT_VOLATILITY)
        pace = min(max(math.exp(drift), _DRIFT_RANGE[0]), _DRIFT_RANGE[1])
        if index > 0:
            t += _gap(prev, stroke, typed, pace, rng, profile)
            if ups[-1] > t and prev is not None and prev.key != stroke.key:
                _settle_rollover(downs, ups, t, rng, profile)
            t = _release_same_key(last_index_by_key.get(stroke.key), t, downs, ups)
        hold_median = profile.hold_median_s
        if stroke.key == KEY_BACKSPACE:
            hold_median *= _BACKSPACE_HOLD_FRAC
        hold = hold_median * math.exp(rng.gauss(0.0, _HOLD_SIGMA))
        downs.append(t)
        ups.append(t + min(max(hold, hold_floor), hold_ceil))
        last_index_by_key[stroke.key] = index
        if stroke.needs_shift:
            _extend_shift_spans(shift_spans, stroke, index)
        typed = typed[:-1] if stroke.key == KEY_BACKSPACE else typed + stroke.char
        prev = stroke

    _keep_unicode_apart(strokes, downs, ups)
    events = [
        KeyEvent(downs[i], True, s.key, s.is_unicode) for i, s in enumerate(strokes)
    ]
    events += [
        KeyEvent(ups[i], False, s.key, s.is_unicode) for i, s in enumerate(strokes)
    ]
    events += _shift_events(shift_spans, downs, ups, rng)
    if not events:
        return []
    # Stable sort keeps a down before an up that lands on the same instant.
    events.sort(key=lambda e: e.t)
    origin = events[0].t
    return [KeyEvent(e.t - origin, e.is_down, e.key, e.is_unicode) for e in events]


def _gap(
    prev: _Keystroke | None,
    stroke: _Keystroke,
    typed: str,
    pace: float,
    rng: random.Random,
    profile: TypingProfile,
) -> float:
    """Time from the previous key down to this one."""
    mult = stroke.gap_mult
    if mult is None:
        mult = _pair_multiplier(prev, stroke)
        is_shift_run_start = stroke.needs_shift and not (prev and prev.needs_shift)
        if is_shift_run_start:
            mult *= _MULT_FIRST_SHIFTED
    sigma = stroke.gap_sigma if stroke.gap_sigma is not None else profile.gap_sigma
    median = max(profile.median_gap_s * mult * pace - _GAP_SHIFT_S, _GAP_MEDIAN_FLOOR_S)
    gap = _GAP_SHIFT_S + median * math.exp(rng.gauss(0.0, sigma))
    gap += _context_pause(typed, rng)
    return max(gap, _GAP_FLOOR_S)


def _release_same_key(
    last_index: int | None,
    t: float,
    downs: list[float],
    ups: list[float],
) -> float:
    """A key cannot go down again before it is released.

    Shortens the earlier hold when possible, otherwise delays this press.
    Returns the press time to use.
    """
    if last_index is None or ups[last_index] + _SAME_KEY_RELEASE_GAP_S <= t:
        return t
    shortened = t - _SAME_KEY_RELEASE_GAP_S
    if shortened >= downs[last_index] + _SAME_KEY_MIN_HOLD_S:
        ups[last_index] = shortened
        return t
    return ups[last_index] + _SAME_KEY_RELEASE_GAP_S


def _extend_shift_spans(
    spans: list[tuple[str, int, int]],
    stroke: _Keystroke,
    index: int,
) -> None:
    if spans and spans[-1][2] == index - 1:
        key, first, _ = spans[-1]
        spans[-1] = (key, first, index)
        return
    spans.append((_shift_key_for(stroke), index, index))


def _settle_rollover(
    downs: list[float],
    ups: list[float],
    next_down: float,
    rng: random.Random,
    profile: TypingProfile,
) -> None:
    """Slow typists usually lift a key before pressing the next one."""
    if rng.random() < profile.rollover_keep_p:
        return
    early = next_down - rng.uniform(*_EARLY_RELEASE_RANGE_S)
    ups[-1] = max(early, downs[-1] + _MIN_HOLD_S)


def _keep_unicode_apart(
    strokes: list[_Keystroke],
    downs: list[float],
    ups: list[float],
) -> None:
    """Unicode keys borrow a spare keycode, so they never overlap others.

    Earlier keys are released before a Unicode key goes down, and nothing
    goes down until it is released. Holds are shortened where they can be,
    otherwise the rest of the timeline is delayed.
    """
    for j, stroke in enumerate(strokes):
        if not stroke.is_unicode:
            continue
        for i in range(max(0, j - _UNICODE_LOOKBACK), j):
            _release_before(i, j, downs, ups)
        if j + 1 < len(strokes):
            _release_before(j, j + 1, downs, ups)


def _release_before(
    earlier: int,
    later: int,
    downs: list[float],
    ups: list[float],
) -> None:
    if ups[earlier] + _SAME_KEY_RELEASE_GAP_S <= downs[later]:
        return
    shortened = downs[later] - _SAME_KEY_RELEASE_GAP_S
    if shortened >= downs[earlier] + _MIN_HOLD_S:
        ups[earlier] = shortened
        return
    delay = ups[earlier] + _SAME_KEY_RELEASE_GAP_S - downs[later]
    for k in range(later, len(downs)):
        downs[k] += delay
        ups[k] += delay


def _shift_events(
    spans: list[tuple[str, int, int]],
    downs: list[float],
    ups: list[float],
    rng: random.Random,
) -> list[KeyEvent]:
    events: list[KeyEvent] = []
    for key, first, last in spans:
        prev_down = downs[first - 1] if first > 0 else downs[first] - 1.0
        budget = downs[first] - prev_down
        lead = _SHIFT_LEAD_MEDIAN_S * math.exp(rng.gauss(0.0, _SHIFT_LEAD_SIGMA))
        lead = min(max(lead, _SHIFT_LEAD_MIN_S), budget * _SHIFT_LEAD_MAX_FRAC)
        release = ups[last] + rng.gauss(_SHIFT_RELEASE_MU_S, _SHIFT_RELEASE_SD_S)
        release = max(release, downs[last] + _SHIFT_RELEASE_MIN_AFTER_DOWN_S)
        if last + 1 < len(downs):
            release = min(release, downs[last + 1] - _SHIFT_RELEASE_BEFORE_NEXT_S)
        events.append(KeyEvent(downs[first] - lead, True, key))
        events.append(KeyEvent(release, False, key))
    return events


def replay_text(events: list[KeyEvent]) -> str:
    """What the events type into an empty field. Used to check plans."""
    shift_down: set[str] = set()
    text = ""
    for event in events:
        if event.key in SHIFT_KEYS:
            if event.is_down:
                shift_down.add(event.key)
            else:
                shift_down.discard(event.key)
            continue
        if not event.is_down:
            continue
        if event.key == KEY_BACKSPACE:
            text = text[:-1]
            continue
        text += _produced_char(event, bool(shift_down))
    return text


def _produced_char(event: KeyEvent, is_shifted: bool) -> str:
    if event.is_unicode:
        return event.key
    if event.key in _SPECIAL_CHARS:
        return _SPECIAL_CHARS[event.key]
    if not is_shifted:
        return event.key
    if event.key.isalpha():
        return event.key.upper()
    return _UNSHIFTED_TO_SYMBOL.get(event.key, event.key)
