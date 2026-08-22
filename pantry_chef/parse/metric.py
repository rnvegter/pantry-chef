"""Convert imperial amounts to metric.

Volume-to-mass is the part that needs care. A cup is a unit of volume, so
"1 cup" is always 240 ml -- but nobody weighs out 240 ml of flour, they want
125 g. The conversion is therefore ingredient-aware: known dry goods convert to
grams via a density table, liquids convert to millilitres, and anything we
cannot identify falls back to millilitres, which is always literally correct
even when it is not what a baker would write.

Lines that are already metric are returned untouched. Rewriting "400 g" into
"400 g" risks mangling the original text for no gain.
"""

from __future__ import annotations

import re

from .ingredients import canonicalize, parse_ingredient_line
from .quantities import normalize_text, parse_quantity

# --- unit facts ------------------------------------------------------------

ML_PER_UNIT: dict[str, float] = {
    "tsp": 5.0,
    "tbsp": 15.0,
    "cup": 240.0,
    "fl oz": 29.5735,
    "pint": 473.176,      # US pint; a UK pint is 568 ml
    "quart": 946.353,
    "gallon": 3785.41,
}

G_PER_UNIT: dict[str, float] = {
    "oz": 28.3495,
    "lb": 453.592,
    "stick": 113.4,       # a US stick of butter
}

CM_PER_UNIT: dict[str, float] = {"inch": 2.54}

# Units that are already metric, or are counts rather than measures.
METRIC_OR_COUNT: frozenset[str] = frozenset({
    "g", "kg", "ml", "l", "cm", "clove", "bunch", "sprig", "stalk", "head",
    "slice", "piece", "can", "jar", "package", "pinch", "dash", "handful",
    "sheet", "fillet", "rasher",
})

# Grams in one cup. Sourced from standard baking references; where sources
# disagree (flour especially) the mid-range figure is used.
GRAMS_PER_CUP: dict[str, float] = {
    "flour": 125, "bread flour": 130, "self-rising flour": 125,
    "almond flour": 96, "cornstarch": 120, "cocoa": 100, "semolina": 167,
    "sugar": 200, "brown sugar": 220, "powdered sugar": 120,
    "honey": 340, "maple syrup": 322, "syrup": 328, "molasses": 337,
    "butter": 227, "ghee": 205,
    "rice": 185, "basmati rice": 185, "arborio rice": 200, "brown rice": 190,
    "jasmine rice": 185, "wild rice": 160, "quinoa": 170, "couscous": 173,
    "bulgur": 140, "barley": 200, "farro": 200, "polenta": 157, "cornmeal": 157,
    "oat": 90, "oatmeal": 90,
    "lentil": 192, "red lentil": 192, "chickpea": 164, "black bean": 172,
    "kidney bean": 177, "cannellini bean": 177, "butter bean": 170, "pea": 145,
    "breadcrumb": 108, "panko": 60,
    "almond": 143, "walnut": 117, "pecan": 109, "cashew": 137, "pistachio": 123,
    "hazelnut": 135, "peanut": 146, "pine nut": 135, "macadamia": 134,
    "sesame seed": 144, "sunflower seed": 140, "pumpkin seed": 129,
    "chocolate chip": 170, "dark chocolate": 170, "chocolate": 170,
    "raisin": 145, "currant": 144, "date": 147, "coconut": 93,
    "parmesan": 90, "cheddar": 113, "mozzarella": 112, "feta": 150,
    "cheese": 113, "ricotta": 246,
    "salt": 273, "yogurt": 245, "greek yogurt": 245, "sour cream": 230,
    "cream cheese": 232, "mayonnaise": 220, "peanut butter": 258, "tahini": 240,
    "tomato puree": 262, "canned tomato": 240, "olive": 135, "caper": 151,
    "mushroom": 70, "spinach": 30, "kale": 67, "onion": 160, "carrot": 128,
    "potato": 150, "corn": 154, "green bean": 110, "broccoli": 91,
}

# Ingredients measured by volume that should stay volume.
LIQUIDS: frozenset[str] = frozenset({
    "water", "milk", "buttermilk", "cream", "heavy cream", "light cream",
    "stock", "chicken stock", "beef stock", "vegetable stock", "fish stock",
    "dashi", "wine", "white wine", "red wine", "beer", "cider", "sherry",
    "port", "vermouth", "brandy", "rum", "whiskey", "vodka", "sake", "mirin",
    "oil", "olive oil", "vegetable oil", "sesame oil", "coconut oil",
    "peanut oil", "canola oil", "sunflower oil", "vinegar",
    "balsamic vinegar", "white wine vinegar", "red wine vinegar",
    "rice vinegar", "apple cider vinegar", "sherry vinegar",
    "soy sauce", "fish sauce", "worcestershire sauce", "hot sauce",
    "coconut milk", "soy milk", "almond milk", "juice", "lemon juice",
    "lime juice", "orange juice", "espresso", "coffee", "tea", "passata",
})


def _round_ml(value: float) -> int:
    """Round millilitres to something a cook would actually measure."""
    if value < 20:
        return int(round(value))
    if value < 100:
        return int(round(value / 5) * 5)
    if value < 1000:
        return int(round(value / 10) * 10)
    return int(round(value / 50) * 50)


def _round_g(value: float) -> int:
    """Round grams likewise."""
    if value < 20:
        return int(round(value))
    if value < 100:
        return int(round(value / 5) * 5)
    if value < 1000:
        return int(round(value / 5) * 5)
    return int(round(value / 25) * 25)


def _format(value: float, unit: str) -> str:
    """Render an amount, promoting to kg or litres when it gets large."""
    if unit == "g" and value >= 1000:
        litres = value / 1000
        return f"{litres:.2f}".rstrip("0").rstrip(".") + " kg"
    if unit == "ml" and value >= 1000:
        litres = value / 1000
        return f"{litres:.2f}".rstrip("0").rstrip(".") + " l"
    return f"{int(value)} {unit}"


def convert_amount(quantity: float, unit: str, ingredient: str = "",
                   canonical: str | None = None) -> str | None:
    """Convert one amount to metric, or None if it is already metric.

    The ingredient decides whether a volume becomes grams or millilitres.
    Pass `canonical` when it has already been resolved; otherwise the text in
    `ingredient` is canonicalised here.
    """
    if unit in METRIC_OR_COUNT:
        return None

    if canonical is None:
        canonical = canonicalize(ingredient) if ingredient else ""

    if unit in G_PER_UNIT:
        return _format(_round_g(quantity * G_PER_UNIT[unit]), "g")

    if unit in CM_PER_UNIT:
        centimetres = quantity * CM_PER_UNIT[unit]
        text = f"{centimetres:.1f}".rstrip("0").rstrip(".")
        return f"{text} cm"

    if unit in ML_PER_UNIT:
        millilitres = quantity * ML_PER_UNIT[unit]
        # A dry good with a known density is far more useful in grams.
        if canonical in GRAMS_PER_CUP and canonical not in LIQUIDS:
            grams = (millilitres / 240.0) * GRAMS_PER_CUP[canonical]
            return _format(_round_g(grams), "g")
        return _format(_round_ml(millilitres), "ml")

    return None


# Many cookbooks already print metric alongside imperial: "1 lb (454 g) beef",
# "1 (26-oz [737-g]) bag", "(4- to 5-lb [1.8- to 2.3-kg])". Rather than try to
# parse every way a range can be punctuated, take the bracketed text and pick
# the last metric amount inside it -- which is the upper bound of a range, and
# the only amount at all when there is no range.
_BRACKETS_RE = re.compile(r"\(([^()]*(?:\[[^\]]*\])?[^()]*)\)|\[([^\]]*)\]")
_METRIC_AMOUNT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[-–]?\s*(kg|g|ml|l|cm)\b", re.IGNORECASE)
_ANY_BRACKET_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")


def book_metric(raw: str) -> str | None:
    """The metric amount the book itself printed, if it printed one."""
    text = normalize_text(raw)
    best: tuple[float, str] | None = None

    for match in _BRACKETS_RE.finditer(text):
        inside = match.group(1) or match.group(2) or ""
        found = _METRIC_AMOUNT_RE.findall(inside)
        if found:
            value_text, unit = found[-1]      # upper bound of any range
            value = float(value_text.replace(",", "."))
            if value > 0:
                best = (value, unit.lower())

    if best is None:
        return None
    value, unit = best
    text_value = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text_value} {unit}"


# Cheap gate: converting normalises "1½" into "1 1/2", which is a downgrade
# when there was nothing imperial to convert in the first place.
_HAS_IMPERIAL_RE = re.compile(
    r"\b(?:cups?|ounces?|oz|pounds?|lbs?|pints?|quarts?|gallons?|"
    r"inch(?:es)?|sticks?)\b|\d\s*°?\s*F\b|degrees?\s*F|\d\s*\"",
    re.IGNORECASE,
)


def _tidy_spacing(text: str) -> str:
    """Close the gaps left behind when a parenthetical is removed."""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,;:.!?])", r"\1", text)
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    return text.strip(" ,;:-")


def to_metric_line(raw: str) -> str:
    """Rewrite one ingredient line in metric, or return it unchanged.

    Small spoon measures are left alone: "1 tsp salt" is already how every
    metric cookbook writes it, and "5 ml salt" would be worse.
    """
    if not raw or not raw.strip():
        return raw

    leading_quantity, leading_unit, _ = parse_quantity(raw)
    # Teaspoons and tablespoons are used worldwide, and "1 tbsp salt" is far
    # more useful to a cook than "18 g salt". Leave them exactly as written.
    if leading_unit in ("tsp", "tbsp"):
        # The spoons stay, but an imperial aside beside them can still be
        # converted: "6 tablespoons (3 ounces) butter" -> "(85 g)".
        return convert_text(raw)

    printed = book_metric(raw)
    if printed:
        # Use the book's figure and drop the imperial amount it sat beside.
        _q, _u, rest = parse_quantity(_ANY_BRACKET_RE.sub(" ", raw))
        rest = _tidy_spacing(rest)
        return f"{printed} {rest}".strip() if rest else raw

    quantity, unit, rest = parse_quantity(raw)
    if quantity is None or unit is None:
        return convert_text(raw)

    # Ask the ingredient parser what this line is about rather than
    # canonicalising the leftover text here: it strips trailing notes like
    # "plus more for dusting", which would otherwise hide the flour and cost
    # us the density lookup.
    parsed = parse_ingredient_line(raw)
    canonical = parsed[0].canonical if parsed else canonicalize(rest)

    converted = convert_amount(quantity, unit, rest, canonical=canonical)
    if converted is None:
        # No imperial amount at the front, but there may be one inside --
        # "a 9-inch tart tin", "a 2 lb joint".
        return convert_text(raw)

    return f"{converted} {rest}".strip()


# --- prose: oven temperatures and stray imperial measures ------------------

_FAHRENHEIT_RE = re.compile(
    r"(\d{2,3})\s*(?:°\s*F\b|degrees?\s*F(?:ahrenheit)?\b|\bF\b(?!\w))",
    re.IGNORECASE,
)

# Longest alternative first, so "1 1/2" and "1/4" are read whole rather than
# leaving a stray "1/" behind.
_NUM = r"(?:\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+(?:\.\d+)?)"
# A hyphen often joins the amount to the unit: "a 9-inch tin".
_GAP = r"[\s-]*"

_INCH_RE = re.compile(
    rf"({_NUM}){_GAP}(?:inch(?:es)?\b|in\.(?!\w)|\")",
    re.IGNORECASE,
)
_WEIGHT_RE = re.compile(
    rf"({_NUM}){_GAP}(pounds?|lbs?|ounces?|oz)\b",
    re.IGNORECASE,
)
_CUP_RE = re.compile(rf"({_NUM}){_GAP}(cups?)\b", re.IGNORECASE)


# "400°F (200°C)" -- the book already did the conversion, so keep its figure
# and drop the Fahrenheit rather than printing two different Celsius values.
_F_WITH_C_RE = re.compile(
    r"(\d{2,3})\s*(?:°\s*F|degrees?\s*F(?:ahrenheit)?)?\s*"
    r"[(\[]\s*(\d{2,3})\s*(?:°\s*C|degrees?\s*C(?:elsius)?)\s*[)\]]",
    re.IGNORECASE,
)
# Some books add a gas mark too: "200°C (gas mark 6)".
_GAS_MARK_RE = re.compile(r"\s*[(\[]\s*gas(?:\s*mark)?\s*\d+\s*[)\]]", re.IGNORECASE)


def _celsius(fahrenheit: float) -> int:
    """Convert to Celsius and round to the nearest 5, as ovens are marked."""
    return int(round(((fahrenheit - 32) * 5 / 9) / 5) * 5)


def _numeric(text: str) -> float:
    text = text.strip()
    m = re.match(r"(\d+(?:\.\d+)?)\s*(\d+)/(\d+)", text)
    if m:
        return float(m.group(1)) + float(m.group(2)) / float(m.group(3))
    m = re.match(r"(\d+)/(\d+)", text)
    if m:
        return float(m.group(1)) / float(m.group(2))
    m = re.match(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else 0.0


def convert_text(text: str) -> str:
    """Convert imperial measures mentioned in prose, such as oven temperatures."""
    if not text or not _HAS_IMPERIAL_RE.search(text):
        return text

    out = normalize_text(text)
    # The book's own Celsius wins over anything we would compute.
    out = _F_WITH_C_RE.sub(lambda m: f"{m.group(2)}°C", out)
    out = _FAHRENHEIT_RE.sub(lambda m: f"{_celsius(float(m.group(1)))}°C", out)
    out = _INCH_RE.sub(
        lambda m: f"{_round_cm(_numeric(m.group(1)) * 2.54)} cm", out)
    out = _WEIGHT_RE.sub(lambda m: _weight_sub(m), out)
    out = _CUP_RE.sub(
        lambda m: _format(_round_ml(_numeric(m.group(1)) * 240), "ml"), out)
    return out


def _round_cm(value: float) -> str:
    """Round centimetres the way a recipe writes them: a 9-inch tin is 23 cm."""
    if value < 5:
        rounded = round(value * 2) / 2
        return f"{rounded:.1f}".rstrip("0").rstrip(".")
    return str(int(round(value)))


def _weight_sub(match: re.Match[str]) -> str:
    quantity = _numeric(match.group(1))
    unit = match.group(2).lower()
    grams = quantity * (453.592 if unit.startswith(("pound", "lb")) else 28.3495)
    return _format(_round_g(grams), "g")
