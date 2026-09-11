"""Work out how long a recipe takes.

Three sources, in descending order of trust:
  1. an explicit label -- "Total time: 45 minutes", "Ready in 1 hr 10"
  2. labelled parts that add up -- "Prep 15 min" + "Cook 30 min"
  3. durations mentioned in the method, summed along the critical path

Anything unlabelled is an estimate and is flagged as such, so the search can
prefer recipes whose time we actually know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .quantities import normalize_text

# How a duration is written: "45 minutes", "1 hour 10", "1-1/2 hours", "90 mins"
# Alternatives are ordered longest-first: "1-1/2" is a mixed number, not the
# range "1 to 1" followed by a stray "/2".
_NUM = (
    r"(?:\d+\s*[-–]\s*\d+\s*/\s*\d+"      # 1-1/2
    r"|\d+\s+\d+\s*/\s*\d+"                # 1 1/2
    r"|\d+\s*/\s*\d+"                       # 1/2
    r"|\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+(?:[.,]\d+)?)?)"  # 45, 2.5, 2-3
)
_HOUR_WORDS = r"h|hr|hrs|hour|hours|uur"
_MIN_WORDS = r"m|min|mins|minute|minutes|minuten"

_DURATION_RE = re.compile(
    rf"(?P<h>{_NUM})\s*(?:{_HOUR_WORDS})\b(?:\s*(?P<hm>{_NUM})\s*(?:{_MIN_WORDS})\b)?"
    rf"|(?P<m>{_NUM})\s*(?:{_MIN_WORDS})\b",
    re.IGNORECASE,
)

# Labels that introduce a time figure.
_TOTAL_LABELS = r"total time|total|ready in|ready|takes|time in total|overall|from start to finish"
_ACTIVE_LABELS = (
    r"prep(?:aration)? time|prep|hands[- ]on(?: time)?"
    r"|active(?: time)?|work(?:ing)? time"
)
_PASSIVE_LABELS = (
    r"cook(?:ing)? time|cook|bake|baking(?: time)?|oven(?: time)?"
    r"|inactive|resting|rest|chill(?:ing)?|marinat(?:e|ing)"
    r"|rising|proving|proof(?:ing)?"
)

_LABEL_RE = re.compile(
    rf"\b(?P<label>{_TOTAL_LABELS}|{_ACTIVE_LABELS}|{_PASSIVE_LABELS})\b",
    re.IGNORECASE,
)

# Long waits that should not inflate the number a hungry cook filters on.
_OVERNIGHT_RE = re.compile(
    r"\bovernight|\b(?:8|12|24|48)\s*(?:h|hr|hrs|hour|hours)\b|"
    r"\bat least\s+\d+\s*(?:h|hr|hrs|hour|hours)\b|\bfor\s+\d+\s*days?\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RecipeTime:
    """Total and active minutes, plus where the numbers came from."""

    total_minutes: int | None = None
    active_minutes: int | None = None
    source: str = "unknown"        # label | labels-summed | method | estimate
    has_long_wait: bool = False

    @property
    def is_explicit(self) -> bool:
        return self.source in {"label", "labels-summed"}


def _value(text: str) -> float:
    """Read a possibly-fractional, possibly-ranged number; ranges take the top."""
    text = text.strip().replace(",", ".")
    m = re.match(r"(\d+)\s*[-–]\s*(\d+)/(\d+)", text)
    if m:
        return float(m.group(1)) + float(m.group(2)) / float(m.group(3))
    m = re.match(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(2))
    m = re.match(r"(\d+(?:\.\d+)?)\s+(\d+)/(\d+)", text)
    if m:
        return float(m.group(1)) + float(m.group(2)) / float(m.group(3))
    m = re.match(r"(\d+)/(\d+)", text)
    if m:
        return float(m.group(1)) / float(m.group(2))
    m = re.match(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else 0.0


def parse_duration(text: str) -> int | None:
    """Minutes for the first duration in the text, or None."""
    m = _DURATION_RE.search(normalize_text(text))
    if not m:
        return None
    if m.group("h"):
        minutes = _value(m.group("h")) * 60
        if m.group("hm"):
            minutes += _value(m.group("hm"))
        return round(minutes)
    return round(_value(m.group("m")))


def all_durations(text: str) -> list[int]:
    """Every duration in the text, in order, as minutes."""
    out: list[int] = []
    for m in _DURATION_RE.finditer(normalize_text(text)):
        if m.group("h"):
            minutes = _value(m.group("h")) * 60
            if m.group("hm"):
                minutes += _value(m.group("hm"))
        else:
            minutes = _value(m.group("m"))
        if 0 < minutes <= 60 * 72:
            out.append(round(minutes))
    return out


def _classify(label: str) -> str:
    lowered = label.lower()
    if re.fullmatch(_TOTAL_LABELS, lowered, re.IGNORECASE):
        return "total"
    if re.fullmatch(_ACTIVE_LABELS, lowered, re.IGNORECASE):
        return "active"
    return "passive"


def extract_time(header_text: str, instructions: str = "") -> RecipeTime:
    """Best available reading of how long a recipe takes.

    `header_text` is the metadata region near the title, where explicit labels
    live; `instructions` is the method, mined only as a fallback.
    """
    result = RecipeTime()
    result.has_long_wait = bool(
        _OVERNIGHT_RE.search(header_text) or _OVERNIGHT_RE.search(instructions)
    )

    totals: list[int] = []
    actives: list[int] = []
    passives: list[int] = []

    header = normalize_text(header_text)
    labels = list(_LABEL_RE.finditer(header))
    for i, m in enumerate(labels):
        # The figure belongs to this label only up to where the next one starts.
        stop = labels[i + 1].start() if i + 1 < len(labels) else len(header)
        minutes = parse_duration(header[m.end():min(stop, m.end() + 40)])
        if minutes is None or minutes <= 0:
            continue
        kind = _classify(m.group("label"))
        (totals if kind == "total" else actives if kind == "active" else passives).append(minutes)

    if totals:
        result.total_minutes = max(totals)
        result.active_minutes = max(actives) if actives else None
        result.source = "label"
        return result

    if actives or passives:
        result.total_minutes = sum(actives[:1]) + sum(passives[:1]) or None
        result.active_minutes = actives[0] if actives else None
        result.source = "labels-summed"
        if result.total_minutes:
            return result

    # No labels. Fall back to durations named in the method. Steps run in
    # sequence, so the sum is the better estimate -- but cap the runaway
    # "chill overnight" cases, which are waiting rather than cooking.
    durations = [d for d in all_durations(instructions) if d <= 240]
    if durations:
        result.total_minutes = min(sum(durations), 8 * 60)
        result.active_minutes = None
        result.source = "method"
        return result

    if instructions.strip():
        # Nothing numeric at all: guess from how much work the method describes.
        steps = max(1, len([s for s in re.split(r"[.\n]", instructions) if s.strip()]))
        result.total_minutes = int(min(120, max(10, steps * 4)))
        result.source = "estimate"

    return result


# --- timers for cook mode -----------------------------------------------------
#
# Cook mode turns "simmer for 20 minutes" into a button that starts a timer.
# That needs the position of each duration in the step as displayed, so unlike
# the functions above this reads the text as it is, without normalising it
# first — normalising "2½" to "2 1/2" would shift every offset after it.

_TIMER_NUM = (
    r"(?:\d+\s*[-–]\s*\d+\s*/\s*\d+"         # 1-1/2
    r"|\d+\s+\d+\s*/\s*\d+"                   # 1 1/2
    r"|\d+\s*/\s*\d+"                         # 1/2
    r"|\d+(?:[.,]\d+)?\s*[½¼¾⅓⅔]?"            # 20, 2.5, 2½
    r"|[½¼¾⅓⅔])"                              # ½
)
_TIMER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty-five": 45, "sixty": 60, "ninety": 90,
}
_TIMER_AMOUNT = (
    rf"(?:{_TIMER_NUM}|{'|'.join(sorted(_TIMER_WORDS, key=len, reverse=True))})")
_SECONDS_PER = {"h": 3600, "m": 60, "s": 1}

# The number and the unit must be separated by space, not a hyphen: "a
# 15-minute walk" and "30-second bursts" describe something, they are not an
# instruction to wait.
_TIMER_RE = re.compile(
    rf"(?<![\w-])(?:(?P<half>half an hour)"
    rf"|(?P<a>{_TIMER_AMOUNT})(?:\s*(?:-|–|—|\bto\b|\bor\b)\s*(?P<b>{_TIMER_AMOUNT}))?"
    r"\s*(?P<unit>hours?|hrs?|h|minutes?|mins?|seconds?|secs?)\b"
    rf"(?:,?\s*(?:and\s+)?(?P<a2>{_TIMER_NUM})\s*(?P<unit2>minutes?|mins?|seconds?|secs?)\b)?)",
    re.IGNORECASE,
)

# Longer than this is a wait, not a timer: "refrigerate for at least 24 hours".
_TIMER_MAX_SECONDS = 12 * 3600


def _timer_value(text: str) -> float:
    lowered = text.strip().lower()
    if lowered in _TIMER_WORDS:
        return float(_TIMER_WORDS[lowered])
    return _value(normalize_text(text).strip())


def _timer_label(seconds: int) -> str:
    if seconds >= 3600:
        hours, rest = divmod(seconds, 3600)
        return f"{hours} h {rest // 60} min" if rest >= 60 else f"{hours} h"
    if seconds >= 60:
        minutes, rest = divmod(seconds, 60)
        if rest == 0:
            return f"{minutes} min"
        return f"{minutes}½ min" if rest == 30 else f"{minutes} min {rest} s"
    return f"{seconds} s"


def _range_label(low: int, high: int | None) -> str:
    if not high or high == low:
        return _timer_label(low)
    # "2–3 min" reads better than "2 min – 3 min" when the units agree.
    low_text, high_text = _timer_label(low), _timer_label(high)
    low_parts, high_parts = low_text.split(" "), high_text.split(" ")
    if len(low_parts) == len(high_parts) == 2 and low_parts[1] == high_parts[1]:
        return f"{low_parts[0]}–{high_text}"
    return f"{low_text} – {high_text}"


def _utf16(text: str, index: int) -> int:
    """A Python string index as a JavaScript one, which counts UTF-16 units."""
    return len(text[:index].encode("utf-16-le")) // 2


def find_timers(text: str) -> list[dict]:
    """Every duration in one method step that is worth a timer.

    Each timer carries its position in `text` (in UTF-16 units, as the browser
    counts), the seconds to set it for, and a short label. A range such as
    "8 to 10 minutes" is set for its lower end — the moment to start checking —
    with the upper end kept alongside.
    """
    timers = []
    for m in _TIMER_RE.finditer(text):
        if m.group("half"):
            low, high = 1800, None
        else:
            per = _SECONDS_PER[m.group("unit")[0].lower()]
            low_f = _timer_value(m.group("a")) * per
            high_f = _timer_value(m.group("b")) * per if m.group("b") else None
            if m.group("a2"):
                extra = _timer_value(m.group("a2")) * _SECONDS_PER[m.group("unit2")[0].lower()]
                low_f += extra
                high_f = high_f + extra if high_f is not None else None
            low = round(low_f)
            high = round(high_f) if high_f is not None else None
        if not 5 <= low <= _TIMER_MAX_SECONDS:
            continue
        timers.append({
            "start": _utf16(text, m.start()),
            "end": _utf16(text, m.end()),
            "seconds": low,
            "max_seconds": high if high and high > low else None,
            "label": _range_label(low, high if high and high > low else None),
        })
    return timers
