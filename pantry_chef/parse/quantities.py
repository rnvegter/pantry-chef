"""Pull the amount and unit off the front of an ingredient line.

Cookbooks write amounts every way imaginable: "1 1/2 cups", "1½ cups",
"2-3 tbsp", "100g", "a pinch of", "Two large onions". This module reduces all
of that to (quantity, unit, remainder) and never raises -- an unparseable line
just comes back with quantity None and the text untouched.
"""

from __future__ import annotations

import re
import unicodedata

from .lexicon import QUANTITY_WORDS, UNITS, VULGAR_FRACTIONS

_WORD_NUMBERS = {
    "a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0,
    "five": 5.0, "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0,
    "ten": 10.0, "eleven": 11.0, "twelve": 12.0, "dozen": 12.0,
    "half": 0.5, "quarter": 0.25, "third": 1 / 3, "couple": 2.0,
    # Deliberately vague words still signal "this line has an amount".
    "few": 3.0, "several": 3.0, "some": 1.0,
}

# Longest unit spellings first so "tablespoons" wins over "t".
_UNIT_ALTERNATION = "|".join(
    re.escape(u) for u in sorted(UNITS, key=len, reverse=True)
)

_NUMBER = r"\d+(?:[.,]\d+)?"
_FRACTION = r"\d+\s*/\s*\d+"
_VULGAR = "".join(VULGAR_FRACTIONS)

# A quantity: optional whole part, optional fraction (ascii or vulgar),
# optionally a range ("2-3", "2 to 3").
_QTY_ATOM = rf"(?:{_NUMBER}\s*{_FRACTION}|{_NUMBER}\s*[{_VULGAR}]|{_FRACTION}|[{_VULGAR}]|{_NUMBER})"
_QTY_RE = re.compile(
    rf"^\s*(?P<qty>{_QTY_ATOM})"
    rf"(?:\s*(?:-|–|—|to|or)\s*(?P<qty2>{_QTY_ATOM}))?",
    re.IGNORECASE,
)

_UNIT_RE = re.compile(rf"^\s*(?P<unit>{_UNIT_ALTERNATION})\b\.?", re.IGNORECASE)

_WORD_QTY_RE = re.compile(
    rf"^\s*(?P<word>{'|'.join(sorted(QUANTITY_WORDS, key=len, reverse=True))})\b",
    re.IGNORECASE,
)

# "1 x 400g can", "2 × 15ml spoons"
_MULTIPLIER_RE = re.compile(rf"^\s*({_NUMBER})\s*[x×]\s*", re.IGNORECASE)

_LEADING_BULLET_RE = re.compile(r"^[\s ]*[-–—•*·▪●○□■◦‣⁃]+[\s ]*")

# NFKC turns "½" into "1<U+2044>2", which no longer matches VULGAR_FRACTIONS,
# so vulgar fractions are spelled out in ascii before any normalisation runs.
_ASCII_FRACTIONS = {
    "½": " 1/2", "⅓": " 1/3", "⅔": " 2/3", "¼": " 1/4", "¾": " 3/4",
    "⅕": " 1/5", "⅖": " 2/5", "⅗": " 3/5", "⅘": " 4/5", "⅙": " 1/6",
    "⅚": " 5/6", "⅐": " 1/7", "⅛": " 1/8", "⅜": " 3/8", "⅝": " 5/8",
    "⅞": " 7/8", "⅑": " 1/9", "⅒": " 1/10",
}
_FRACTION_TABLE = str.maketrans(_ASCII_FRACTIONS)


def normalize_text(line: str) -> str:
    """Fold a raw line into plain ascii-ish text safe for the amount regexes."""
    return unicodedata.normalize(
        "NFKC", line.translate(_FRACTION_TABLE)
    ).replace("\u2044", "/")


def strip_bullet(line: str) -> str:
    """Remove list bullets and stray leading punctuation."""
    return _LEADING_BULLET_RE.sub("", line).strip()


def _atom_value(text: str) -> float | None:
    """Evaluate one quantity atom such as '1 1/2', '3/4' or '½'."""
    text = text.strip()
    if not text:
        return None

    total = 0.0
    seen = False

    # Vulgar fractions may be glued to a number: "1½".
    for ch in VULGAR_FRACTIONS:
        if ch in text:
            total += VULGAR_FRACTIONS[ch]
            seen = True
            text = text.replace(ch, " ")

    text = text.strip()
    if text:
        m = re.fullmatch(rf"({_NUMBER})?\s*(?:({_NUMBER})\s*/\s*({_NUMBER}))?", text)
        if not m:
            return total if seen else None
        whole, num, den = m.groups()
        if whole:
            total += float(whole.replace(",", "."))
            seen = True
        if num and den and float(den) != 0:
            total += float(num) / float(den)
            seen = True

    return total if seen else None


def parse_quantity(line: str) -> tuple[float | None, str | None, str]:
    """Split an ingredient line into (quantity, canonical unit, remainder).

    The quantity of a range is its midpoint, so "2-3 tbsp" yields 2.5.
    """
    text = strip_bullet(normalize_text(line))
    qty: float | None = None

    mult = _MULTIPLIER_RE.match(text)
    if mult:
        # "1 x 400g can of tomatoes" -- the pack count, then re-read the size.
        qty = float(mult.group(1).replace(",", "."))
        text = text[mult.end():]

    m = _QTY_RE.match(text)
    if m:
        low = _atom_value(m.group("qty"))
        high = _atom_value(m.group("qty2")) if m.group("qty2") else None
        value = low if high is None else (low + high) / 2 if low is not None else high
        if value is not None:
            qty = value if qty is None else qty * value
            text = text[m.end():]
    else:
        wm = _WORD_QTY_RE.match(text)
        if wm:
            word = wm.group("word").lower()
            # Only treat a bare word as a quantity when a unit or noun follows,
            # otherwise "Some notes on stock" becomes an ingredient.
            remainder = text[wm.end():].lstrip()
            if remainder:
                qty = _WORD_NUMBERS.get(word)
                text = remainder

    unit: str | None = None
    um = _UNIT_RE.match(text)
    if um:
        unit = UNITS[um.group("unit").lower().rstrip(".")]
        text = text[um.end():]

    # "2 400g cans" / "400 g can" -- a second amount after the first is a size.
    if unit is None and qty is not None:
        m2 = _QTY_RE.match(text)
        if m2:
            um2 = _UNIT_RE.match(text[m2.end():])
            if um2:
                unit = UNITS[um2.group("unit").lower().rstrip(".")]
                text = text[m2.end() + um2.end():]

    # A pack word may trail the size ("400g can of tomatoes"); it is packaging,
    # not identity, so drop it once a unit is already known.
    if unit is not None:
        leftover = _UNIT_RE.match(text)
        if leftover:
            text = text[leftover.end():]

    text = re.sub(r"^\s*(?:of|de|del)\b\s*", "", text, flags=re.IGNORECASE)
    return qty, unit, text.strip(" \t,;:.-–—")


def has_leading_quantity(line: str) -> bool:
    """True when the line opens with something numeric or unit-like."""
    text = strip_bullet(normalize_text(line))
    if _QTY_RE.match(text) or _MULTIPLIER_RE.match(text):
        return True
    wm = _WORD_QTY_RE.match(text)
    return bool(wm and text[wm.end():].strip())
