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
_ACTIVE_LABELS = r"prep(?:aration)? time|prep|hands[- ]on(?: time)?|active(?: time)?|work(?:ing)? time"
_PASSIVE_LABELS = r"cook(?:ing)? time|cook|bake|baking(?: time)?|oven(?: time)?|inactive|resting|rest|chill(?:ing)?|marinat(?:e|ing)|rising|proving|proof(?:ing)?"

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
        return int(round(minutes))
    return int(round(_value(m.group("m"))))


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
            out.append(int(round(minutes)))
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
