"""Scale a recipe's ingredient amounts up or down.

Only amounts a cook measures out are changed, and only where the line makes it
unambiguous which number is the amount:

  "1½ cups (338 g) cottage cheese"  x2 -> "3 cups (675 g) cottage cheese"
  "1 (14-oz) can tomatoes"          x2 -> "2 (14-oz) cans tomatoes"
  "2 to 3 garlic cloves"            x2 -> "4 to 6 garlic cloves"

The second line is why brackets need care. After a measured amount a bracket
is the same amount in other units, so it scales with it; after a bare count it
is the size of each item, which does not change however many you buy.

Anything this cannot read confidently is left exactly as the book wrote it and
reported as not scaled, so the page can say so rather than print a quiet
wrong number.
"""

from __future__ import annotations

import re

from .lexicon import UNITS, VULGAR_FRACTIONS
from .metric import _round_g, _round_ml
from .quantities import _atom_value

_VULGAR = "".join(VULGAR_FRACTIONS)
_NUMBER = r"\d+(?:[.,]\d+)?"

# One amount, longest spellings first: "1-1/2" and "1 1/2" are mixed numbers,
# not the range "1 to 1" with a stray "/2".
_ATOM = (
    rf"(?:\d+-\d+\s*/\s*\d+"
    rf"|\d+\s+\d+\s*/\s*\d+"
    rf"|{_NUMBER}\s*[{_VULGAR}]"
    rf"|\d+\s*/\s*\d+"
    rf"|[{_VULGAR}]"
    rf"|{_NUMBER})"
)
_RANGE_SEP = r"\s*(?:-\s*to\b|-|–|—|\bto\b|\bor\b)\s*"

_UNIT_ALT = "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))

# An amount at the head of the line, after an optional label ("Garnish:") and
# an optional hedge ("about").
_LEAD_RE = re.compile(
    r"^(?P<pre>\s*(?:[^\W\d][\w ]{0,24}:\s*)?(?:(?:about|approx\.?|approximately|"
    r"roughly|around|scant|heaping|heaped|generous)\s+)?)"
    rf"(?P<a>{_ATOM})(?:(?P<sep>{_RANGE_SEP})(?P<b>{_ATOM}))?",
    re.IGNORECASE,
)
_WORD_LEAD_RE = re.compile(
    r"^(?P<pre>\s*)(?P<word>one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve)\b(?=\s+\w)",
    re.IGNORECASE,
)
_WORD_VALUES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

# What may follow a leading number that means it was never an amount:
# "8-inch tortillas", "5-spice powder", "2% milk", and "1. Preheat the oven",
# a method step the extractor filed among the ingredients.
_NOT_AN_AMOUNT_RE = re.compile(
    r"^(?:-[^\W\d]|[.)](?:\s|$)|\s*(?:inch(?:es)?\b|in\.|\"|″|%|°|cm\b))", re.IGNORECASE)

_METRIC_UNIT_RE = re.compile(r"^(?P<gap>\s*)(?P<unit>kg|g|ml|l)\b\.?", re.IGNORECASE)
_UNIT_RE = re.compile(rf"^(?P<gap>\s*)(?P<unit>{_UNIT_ALT})\b\.?", re.IGNORECASE)
_MULTIPLIER_RE = re.compile(r"^\s*[x×]\s*", re.IGNORECASE)

# An amount inside a bracket or after a slash: with a unit ("338 g", "3 cups")
# or counting something ("about 4 parsnips").
_AMOUNT_UNIT_RE = re.compile(
    rf"(?<![\w/.,])(?P<a>{_ATOM})(?:(?P<sep>{_RANGE_SEP})(?P<b>{_ATOM}))?"
    rf"(?:(?P<gap>\s*-?\s*)(?P<unit>{_UNIT_ALT})\b|(?=\s+[^\W\d]))",
    re.IGNORECASE,
)
_LENGTHS = frozenset({"inch", "cm"})
_BRACKET_RE = re.compile(r"\([^()]*\)|\[[^\]]*\]")
_SLASH_ALT_RE = re.compile(r"^\s*/\s*[^,;()]*")
_OF_RE = re.compile(rf"\b(?:of|from)\s+(?P<a>{_ATOM})(?=\s+[^\W\d])", re.IGNORECASE)
# Packaging that follows a size: "1 (30-oz) bag", "2 (8-oz) blocks".
_PACKAGING = ("bag", "bags", "box", "boxes", "bottle", "bottles", "block", "blocks",
              "carton", "cartons", "container", "containers", "envelope",
              "envelopes", "pouch", "pouches", "tub", "tubs")
_BRACKET_THEN_UNIT_RE = re.compile(
    rf"^\s*(?:\([^()]*\)|\[[^\]]*\])\s*(?P<unit>{_UNIT_ALT}|{'|'.join(_PACKAGING)})\b",
    re.IGNORECASE)

# Units that measure an amount. After one of these, a bracket restates the
# amount; after anything else ("1 can", "2 cloves") there is nothing to restate.
MEASURES = frozenset({
    "tsp", "tbsp", "cup", "oz", "lb", "g", "kg", "ml", "l",
    "pint", "quart", "gallon", "stick",
})

# Spelled-out words that change with the number. Abbreviations (tbsp, oz, g)
# never do.
_PLURALS = {
    "cup": "cups", "teaspoon": "teaspoons", "tablespoon": "tablespoons",
    "ounce": "ounces", "pound": "pounds", "gram": "grams", "kilogram": "kilograms",
    "liter": "liters", "litre": "litres", "milliliter": "milliliters",
    "millilitre": "millilitres", "pint": "pints", "quart": "quarts",
    "gallon": "gallons", "stick": "sticks", "clove": "cloves", "can": "cans",
    "tin": "tins", "jar": "jars", "package": "packages", "packet": "packets",
    "sprig": "sprigs", "slice": "slices", "stalk": "stalks", "head": "heads",
    "piece": "pieces", "sheet": "sheets", "fillet": "fillets", "rasher": "rashers",
    "bunch": "bunches", "pinch": "pinches", "dash": "dashes", "handful": "handfuls",
    "bag": "bags", "box": "boxes", "bottle": "bottles", "block": "blocks",
    "carton": "cartons", "container": "containers", "envelope": "envelopes",
    "pouch": "pouches", "tub": "tubs",
    # Things counted rather than measured.
    "egg": "eggs", "yolk": "yolks", "white": "whites", "onion": "onions",
    "shallot": "shallots", "lemon": "lemons", "lime": "limes", "orange": "oranges",
    "avocado": "avocados", "potato": "potatoes", "tomato": "tomatoes",
    "carrot": "carrots", "banana": "bananas", "apple": "apples", "pear": "pears",
    "peach": "peaches", "plum": "plums", "date": "dates", "fig": "figs",
    "mango": "mangoes", "leaf": "leaves", "tortilla": "tortillas", "bun": "buns",
    "roll": "rolls", "breast": "breasts", "thigh": "thighs",
    "drumstick": "drumsticks", "steak": "steaks", "chop": "chops",
    "sausage": "sausages", "cucumber": "cucumbers", "zucchini": "zucchini",
    "courgette": "courgettes", "jalapeño": "jalapeños", "jalapeno": "jalapenos",
    "chile": "chiles", "chili": "chilies", "chilli": "chillies",
    "scallion": "scallions", "leek": "leeks", "pepper": "peppers",
    "ear": "ears", "bagel": "bagels", "pita": "pitas", "muffin": "muffins",
}
_SINGULARS = {plural: singular for singular, plural in _PLURALS.items()}

_NICE_FRACTIONS = (
    (0.0, ""), (1 / 8, "⅛"), (1 / 4, "¼"), (1 / 3, "⅓"), (1 / 2, "½"),
    (2 / 3, "⅔"), (3 / 4, "¾"), (1.0, ""),
)


def nice_number(value: float) -> str:
    """A number the way a recipe writes it: 1½, ⅔, 12, never 1.3333."""
    if value >= 20:
        return str(round(value))
    if value >= 10:
        halves = round(value * 2) / 2
        return f"{int(halves)}½" if halves % 1 else str(int(halves))

    whole = int(value)
    target, glyph = min(_NICE_FRACTIONS, key=lambda pair: abs(pair[0] - (value - whole)))
    if target == 1.0:
        whole, glyph = whole + 1, ""
    if whole == 0 and not glyph:
        glyph = "⅛"             # a scaled-down pinch is small, not nothing
    return f"{whole or ''}{glyph}"


def _metric(values: list[float], unit: str) -> tuple[list[str], str]:
    """Scaled metric amounts, rounded as a cook measures, sharing one unit."""
    unit = unit.lower()
    grams = unit in ("g", "kg")
    base = [v * (1000 if unit in ("kg", "l") else 1) for v in values]
    rounder = _round_g if grams else _round_ml
    rounded = [max(1, rounder(v)) for v in base]
    if max(rounded) >= 1000:
        # Past a kilo, nobody weighs to the gram: "1.02 kg" of chicken is 1 kg.
        rounded = [round(r / 50) * 50 for r in rounded]
        return ([f"{r / 1000:.2f}".rstrip("0").rstrip(".") for r in rounded],
                "kg" if grams else "l")
    return [str(int(r)) for r in rounded], ("g" if grams else "ml")


def _match_case(word: str, like: str) -> str:
    if like.isupper() and len(like) > 1:
        return word.upper()
    if like[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def _agree(word: str, amount: float) -> str:
    """The singular or plural of `word` to go with `amount`."""
    lowered = word.lower()
    if amount > 1 and lowered in _PLURALS and _PLURALS[lowered] != lowered:
        return _match_case(_PLURALS[lowered], word)
    if amount <= 1 and lowered in _SINGULARS and _SINGULARS[lowered] != lowered:
        return _match_case(_SINGULARS[lowered], word)
    return word


def _values(match: re.Match[str], factor: float) -> list[float]:
    values = [_atom_value(match.group("a").replace("-", " "))]
    if match.group("b"):
        values.append(_atom_value(match.group("b").replace("-", " ")))
    return [v * factor for v in values if v is not None]


def _render(match: re.Match[str], scaled: list[float]) -> str:
    text = nice_number(scaled[0])
    if len(scaled) > 1:
        text += match.group("sep") + nice_number(scaled[1])
    return text


def _canonical(unit: str) -> str:
    return (UNITS.get(unit.lower().rstrip(".")) or UNITS.get(unit.lower())
            or UNITS.get(unit, ""))


def _scale_amount_unit(match: re.Match[str], factor: float, *, counted: bool) -> str:
    """One amount inside a bracket or after a slash.

    Lengths never scale — a tin is 23 cm however many cakes you bake. Nor does
    a hyphenated size: "half a 13.5-ounce can" is still a 13.5-ounce can. A
    hyphenated amount closing its bracket, as in "½ cup (28-g)", is only the
    book's way of writing a restatement, and scales like one.
    """
    unit = match.group("unit")
    hyphenated = "-" in (match.group("gap") or "")
    names_a_thing = bool(re.match(r"\s*[^\W\d]", match.string[match.end():]))
    if unit and (_canonical(unit) in _LENGTHS
                 or (hyphenated and (counted or names_a_thing))):
        return match.group(0)
    scaled = _values(match, factor)
    if not scaled:
        return match.group(0)
    if not unit:
        return _render(match, scaled)
    if unit.lower() in ("g", "kg", "ml", "l"):
        numbers, out_unit = _metric(scaled, unit)
        sep = match.group("sep") or ""
        return sep.join(numbers) + " " + out_unit
    return _render(match, scaled) + match.group("gap") + _agree(unit, max(scaled))


def _scale_restatements(rest: str, factor: float, *, counted: bool = False) -> str:
    """Scale the amounts that restate the line's own: "(338 g)", "/ 625g",
    "(about 4 parsnips)", "(about 3 cups)"."""
    def amounts(text: str, count: int = 0) -> str:
        return _AMOUNT_UNIT_RE.sub(
            lambda a: _scale_amount_unit(a, factor, counted=counted), text, count=count)

    slash = _SLASH_ALT_RE.match(rest)
    if slash:
        rest = amounts(slash.group(0), count=1) + rest[slash.end():]
    return _BRACKET_RE.sub(lambda m: amounts(m.group(0)), rest)


def _agree_head_noun(rest: str, amount: float) -> str:
    """Make the counted thing agree: "1 large egg" x2 -> "2 large eggs".

    The counted thing is the last word before the first comma or bracket —
    "2 egg yolks" is counting yolks, not eggs.
    """
    cut = re.search(r"[,;(\[]|\bor\b|\band\b", rest)
    head = rest[:cut.start()] if cut else rest
    words = list(re.finditer(r"[^\W\d_]+(?:-[^\W\d_]+)*", head))
    if not words:
        return rest
    last = words[-1]
    return rest[:last.start()] + _agree(last.group(0), amount) + rest[last.end():]


def scale_line(line: str, factor: float) -> tuple[str, bool]:
    """Scale the amounts in one ingredient line.

    Returns the new line and whether anything was scaled. At a factor of 1 the
    line comes back untouched.
    """
    if not line or factor == 1:
        return line, False

    lead = _LEAD_RE.match(line)
    if lead and not _NOT_AN_AMOUNT_RE.match(line[lead.end():]):
        scaled = _values(lead, factor)
        if scaled:
            return _scale_lead(line, lead, scaled, factor), True

    word = _WORD_LEAD_RE.match(line)
    if word:
        amount = _WORD_VALUES[word.group("word").lower()] * factor
        rest = line[word.end():]
        unit = _UNIT_RE.match(rest)
        if unit:
            rest = (unit.group("gap") + _agree(unit.group("unit"), amount)
                    + rest[unit.end():])
        else:
            rest = _agree_head_noun(rest, amount)
        return word.group("pre") + nice_number(amount) + rest, True

    # "Grated zest and juice of 1 orange"
    of = _OF_RE.search(line)
    if of:
        value = _atom_value(of.group("a"))
        if value:
            amount = value * factor
            rest = _agree_head_noun(line[of.end():], amount)
            return (line[:of.start("a")] + nice_number(amount) + rest), True

    # Some books put the amount last, in brackets: "Edamame (2 handfuls)".
    bracketed = _scale_restatements(line, factor)
    if bracketed != line:
        return bracketed, True

    return line, False


def _scale_lead(line: str, lead: re.Match[str], scaled: list[float], factor: float) -> str:
    rest = line[lead.end():]
    pre = lead.group("pre")

    if _MULTIPLIER_RE.match(rest):
        # "2 x 400 g tins": the count scales, the size of each tin does not.
        return pre + _render(lead, scaled) + rest

    metric = _METRIC_UNIT_RE.match(rest)
    if metric:
        numbers, metric_unit = _metric(scaled, metric.group("unit"))
        sep = lead.group("sep") or ""
        amount = sep.join(numbers) + " " + metric_unit
        return pre + amount + _scale_restatements(rest[metric.end():], factor)

    unit = _UNIT_RE.match(rest)
    if unit:
        after = rest[unit.end():]
        agreed = unit.group("gap") + _agree(unit.group("unit"), max(scaled))
        after = _scale_restatements(
            after, factor, counted=_canonical(unit.group("unit")) not in MEASURES)
        return pre + _render(lead, scaled) + agreed + after

    # A bare count. A bracket straight after it is the size of each item, so it
    # stays; a unit after that bracket is packaging and takes the plural. Later
    # brackets restate the total — "(about 3 cups)" — and scale with it.
    packaged = _BRACKET_THEN_UNIT_RE.match(rest)
    if packaged:
        start, end = packaged.span("unit")
        head = rest[:start] + _agree(packaged.group("unit"), max(scaled))
        rest = head + _scale_restatements(rest[end:], factor, counted=True)
    else:
        size = re.match(r"^\s*(?:\([^()]*\)|\[[^\]]*\])", rest)
        head, tail = (rest[:size.end()], rest[size.end():]) if size else ("", rest)
        tail = _scale_restatements(_agree_head_noun(tail, max(scaled)), factor, counted=True)
        rest = head + tail
    return pre + _render(lead, scaled) + rest


# --- servings -----------------------------------------------------------------

_FIRST_NUMBER_RE = re.compile(r"\d+")


def servings_base(text: str | None) -> int | None:
    """The number of servings the book's amounts are written for.

    "4 to 6" is written for 4: the lower figure is the honest one to scale
    from, and scaling from it errs towards a little too much food.
    """
    if not text:
        return None
    match = _FIRST_NUMBER_RE.search(text)
    if not match:
        return None
    value = int(match.group(0))
    return value if value > 0 else None


def scale_servings(text: str | None, factor: float) -> str:
    """"4 to 6" x2 -> "8 to 12"; "8 scones" x2 -> "16 scones"."""
    if not text or factor == 1:
        return text or ""
    return re.sub(
        rf"{_ATOM}",
        lambda m: nice_number((_atom_value(m.group(0).replace("-", " ")) or 0) * factor),
        text,
    )
