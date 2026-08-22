"""Turn a raw ingredient line into a canonical, matchable ingredient.

The canonical form is what the pantry search joins on, so it has to be stable
across the many ways a book can name the same thing: "spring onions, finely
sliced", "4 green onions" and "scallion" must all land on `scallion`.

Unknown ingredients are never dropped. If nothing in the lexicon matches, the
cleaned phrase becomes its own canonical key, so an obscure ingredient still
indexes and still matches when the user types the same words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .lexicon import (
    COOKING_VERBS,
    DESCRIPTORS,
    EXACT_SYNONYMS,
    HEAD_NOUNS,
    INGREDIENT_NOUNS_SPACED,
    NOTE_MARKERS,
    STAPLES,
    SUBHEAD_MARKERS,
    SYNONYMS,
    UNITS,
)
from .quantities import has_leading_quantity, normalize_text, parse_quantity

# Multi-word lexicon entries, longest first, for containment matching.
_MULTIWORD = sorted(
    (n for n in INGREDIENT_NOUNS_SPACED if " " in n), key=len, reverse=True
)

_IRREGULAR_SINGULARS = {
    "leaves": "leaf", "loaves": "loaf", "halves": "half", "knives": "knife",
    "potatoes": "potato", "tomatoes": "tomato", "anchovies": "anchovy",
    "berries": "berry", "cherries": "cherry", "chillies": "chili",
    "chilies": "chili", "peas": "pea", "molasses": "molasses",
    "asparagus": "asparagus", "couscous": "couscous", "hummus": "hummus",
    "greens": "greens", "oats": "oat", "grits": "grits", "capers": "caper",
}

_NEVER_SINGULARISE = frozenset(
    {"molasses", "asparagus", "couscous", "hummus", "greens", "grits",
     "watercress", "swiss", "brussels", "grass", "bass", "sea bass"}
)

_PAREN_RE = re.compile(r"\([^)]*\)")
_PUNCT_RE = re.compile(r"[^a-z0-9' ]+")
# "basil, 24" -- an index entry, not an ingredient.
_INDEX_ENTRY_RE = re.compile(r"^[^,]{2,40},\s*\d+(?:\s*[-–,]\s*\d+)*$")
# Words that stay lowercase in Title Case and so must not count towards it.
_MINOR_WORDS = frozenset(
    {"a", "an", "the", "and", "or", "of", "with", "in", "on", "for", "to",
     "from", "at", "by", "de", "la", "le", "el", "con", "alla", "all"}
)
_NONWORD_RE = re.compile(r"[^a-z0-9'\- ]+")


@dataclass(slots=True)
class ParsedIngredient:
    """One ingredient line, reduced to its searchable parts."""

    raw: str
    canonical: str
    display: str
    quantity: float | None = None
    unit: str | None = None
    note: str = ""
    is_staple: bool = False


def singularize(word: str) -> str:
    """Crude English singulariser -- good enough for food nouns."""
    lower = word.lower()
    if lower in _NEVER_SINGULARISE:
        return lower
    if lower in _IRREGULAR_SINGULARS:
        return _IRREGULAR_SINGULARS[lower]
    if len(lower) <= 3 or not lower.endswith("s"):
        return lower
    if lower.endswith("ss") or lower.endswith("us") or lower.endswith("is"):
        return lower
    if lower.endswith("ies"):
        return lower[:-3] + "y"
    if lower.endswith(("ches", "shes", "xes", "zes", "sses")):
        return lower[:-2]
    if lower.endswith("oes"):
        return lower[:-2]
    return lower[:-1]


def _strip_note(phrase: str) -> tuple[str, str]:
    """Split trailing handling instructions off the ingredient itself."""
    note_parts: list[str] = []

    lowered = phrase.lower()
    cut = len(phrase)
    for marker in NOTE_MARKERS:
        idx = lowered.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    if cut < len(phrase):
        note_parts.append(phrase[cut:].strip(" ,;"))
        phrase = phrase[:cut]

    # A comma usually separates the ingredient from how it is prepared.
    if "," in phrase:
        head, _, tail = phrase.partition(",")
        # Keep the tail when it is part of the name ("beans, cannellini").
        tail_words = [w for w in re.split(r"\W+", tail.lower()) if w]
        if tail_words and not any(
            w in INGREDIENT_NOUNS_SPACED or w in HEAD_NOUNS for w in tail_words
        ):
            note_parts.append(tail.strip())
            phrase = head

    return phrase.strip(), "; ".join(p for p in note_parts if p)


def clean_phrase(phrase: str) -> str:
    """Reduce a phrase to bare ingredient words: no sizes, states or noise."""
    text = normalize_text(phrase).lower()
    text = _PAREN_RE.sub(" ", text)
    text = _NONWORD_RE.sub(" ", text)

    words: list[str] = []
    for word in text.split():
        word = word.strip("-'")
        if not word:
            continue
        if word in DESCRIPTORS or word.replace("-", " ") in DESCRIPTORS:
            continue
        # A unit surviving this far is leftover packaging language.
        if word in UNITS and word not in INGREDIENT_NOUNS_SPACED:
            continue
        if word in {"of", "and", "or", "the", "a", "an", "with", "into", "in",
                    "on", "to", "for", "from", "such", "as", "any", "very"}:
            continue
        words.append(singularize(word))

    return " ".join(words).strip()


_BRACKET_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")


def _strip_brackets(text: str) -> str:
    """Remove parenthetical asides -- metric equivalents, brands, jokes."""
    return _BRACKET_RE.sub(" ", text)


def light_normal(phrase: str) -> str:
    """Lowercase and singularise without removing descriptors.

    Synonyms are matched against this form first, because some synonym keys are
    built from words that `clean_phrase` would strip. Without it, "beef mince"
    and "minced beef" canonicalise differently, which splits one ingredient
    into two and quietly halves its match rate.
    """
    text = _PUNCT_RE.sub(" ", _strip_brackets(normalize_text(phrase).lower()))
    return " ".join(singularize(w) for w in text.split() if w)


def plain_normal(phrase: str) -> str:
    """Lowercase and de-punctuate, keeping plurals intact."""
    text = _PUNCT_RE.sub(" ", _strip_brackets(normalize_text(phrase).lower()))
    return " ".join(text.split())


# Two synonym indexes. The plural-preserving one is consulted first, because a
# few ingredients change meaning when singularised: "peppers" is a vegetable,
# "pepper" is the spice, and collapsing them would make a real ingredient
# vanish into the staples.
_SYNONYM_INDEX: dict[str, str] = {}
_SYNONYM_INDEX_PLAIN: dict[str, str] = {}


def _build_synonym_index() -> None:
    # Exact synonyms go only into the literal index, never the singularising
    # one, so "peppers" resolves without dragging "pepper" along with it.
    for alt, target in EXACT_SYNONYMS.items():
        _SYNONYM_INDEX_PLAIN.setdefault(plain_normal(alt), target)
    for alt, target in SYNONYMS.items():
        _SYNONYM_INDEX.setdefault(light_normal(alt), target)
        _SYNONYM_INDEX_PLAIN.setdefault(plain_normal(alt), target)


_build_synonym_index()


def canonicalize(phrase: str) -> str:
    """Map an ingredient phrase onto its canonical lexicon name."""
    # Try synonyms before anything is stripped away, plurals intact first.
    direct = (_SYNONYM_INDEX_PLAIN.get(plain_normal(phrase))
              or _SYNONYM_INDEX.get(light_normal(phrase)))
    if direct:
        return direct

    cleaned = clean_phrase(phrase)
    if not cleaned:
        return ""

    if cleaned in SYNONYMS:
        return SYNONYMS[cleaned]
    if cleaned in INGREDIENT_NOUNS_SPACED:
        return cleaned

    # Synonyms may be phrased in the plural or with extra words around them.
    for alt, target in SYNONYMS.items():
        if cleaned == clean_phrase(alt):
            return target

    # Longest multi-word lexicon entry contained in the phrase wins:
    # "smoked streaky bacon" has no multi-word hit, "chicken thigh fillets" does.
    for entry in _MULTIWORD:
        if re.search(rf"\b{re.escape(entry)}\b", cleaned):
            return entry

    # Otherwise consider the head noun. Collapsing "streaky bacon" to "bacon"
    # is right, but collapsing "garlic powder" to "powder" or "garlic" is not:
    # a qualifier sitting in front of a compound fragment changes what the
    # ingredient *is*. So only collapse when the standalone noun is the last
    # word; if anything trails it, the phrase keeps its own identity.
    words = cleaned.split()
    for index in range(len(words) - 1, -1, -1):
        if words[index] in INGREDIENT_NOUNS_SPACED:
            return words[index] if index == len(words) - 1 else cleaned

    # A bare head-noun fragment ("powder", "extract") is only meaningful as
    # part of a compound, so such phrases stay whole too.
    if words and words[-1] in HEAD_NOUNS and len(words) == 1:
        return words[-1]

    # Unknown ingredient: keep it, keyed by its own cleaned text.
    return cleaned


def split_conjunctions(phrase: str) -> list[str]:
    """Split "salt and pepper" into two ingredients, but only when both sides
    are recognisable -- "macaroni and cheese" must stay one dish."""
    parts = re.split(r"\s+and\s+|\s*&\s*", phrase, flags=re.IGNORECASE)
    if len(parts) < 2:
        return [phrase]

    resolved = [canonicalize(p) for p in parts]
    if all(r in INGREDIENT_NOUNS_SPACED or r in SYNONYMS.values() for r in resolved if r):
        return [p for p in parts if p.strip()]
    return [phrase]


def parse_ingredient_line(line: str) -> list[ParsedIngredient]:
    """Parse one ingredient line into one or more canonical ingredients."""
    raw = line.strip()
    if not raw:
        return []

    quantity, unit, remainder = parse_quantity(raw)
    body, note = _strip_note(remainder)

    results: list[ParsedIngredient] = []
    for part in split_conjunctions(body):
        canonical = canonicalize(part)
        if not canonical:
            continue
        # Parenthetical asides are the book talking to the reader -- a metric
        # equivalent, a brand, a joke -- never part of the ingredient's name.
        display = _PAREN_RE.sub(" ", part)
        display = re.sub(r"\s*\[[^\]]*\]", " ", display)
        display = re.sub(r"\s+", " ", display).strip(" ,;:-") or canonical
        results.append(
            ParsedIngredient(
                raw=raw,
                canonical=canonical,
                display=display.lower(),
                quantity=quantity if len(results) == 0 else None,
                unit=unit if len(results) == 0 else None,
                note=note,
                is_staple=canonical in STAPLES,
            )
        )

    return results


def is_title_case(text: str) -> bool:
    """Whether a line is capitalised like a title rather than a list entry.

    This is what separates the recipe name "Lemon and Garlic Roast Chicken"
    from the ingredient "Salt and freshly ground black pepper" -- both are made
    of food words, but only one capitalises every significant word.
    """
    words = [w for w in re.split(r"[\s/]+", text.strip()) if w]
    significant = [w for w in words if w.lower().strip(",.") not in _MINOR_WORDS]
    if len(significant) < 2:
        return False
    capitalised = sum(1 for w in significant if w[:1].isupper())
    return capitalised >= max(2, int(len(significant) * 0.75))


def ingredient_score(line: str) -> float:
    """Confidence in 0..1 that this line is an ingredient, not prose or a step.

    Used to find the ingredient block inside an otherwise unstructured book.
    """
    text = normalize_text(line).strip()
    if not text or len(text) > 200:
        return 0.0

    lowered = text.lower().strip(" .:-–—")
    words = lowered.split()
    if not words:
        return 0.0

    # Section labels inside the list ("For the sauce") are structure, not food.
    if lowered.startswith("for the") or lowered in SUBHEAD_MARKERS:
        return 0.0
    # Index entries pair a food with a page number.
    if _INDEX_ENTRY_RE.match(text.strip()):
        return 0.0

    score = 0.0
    leading_quantity = has_leading_quantity(text)

    if leading_quantity:
        score += 0.5
    _, unit, remainder = parse_quantity(text)
    if unit is not None:
        score += 0.2

    canonical = canonicalize(remainder or text)
    if canonical in INGREDIENT_NOUNS_SPACED or canonical in SYNONYMS.values():
        score += 0.4
    elif any(w in HEAD_NOUNS for w in clean_phrase(text).split()):
        score += 0.25

    # A title-cased line carrying no digits and no unit is a recipe name.
    # The digit test matters because "Five-Minute Yoghurt Flatbreads" opens
    # with a word that also reads as a quantity.
    if is_title_case(text) and not any(c.isdigit() for c in text) and unit is None:
        score -= 0.6
    elif not leading_quantity and is_title_case(text):
        score -= 0.3

    # Instruction steps start with a verb and run long.
    if words[0].rstrip(",.") in COOKING_VERBS:
        score -= 0.45
    if len(words) > 14:
        score -= 0.3
    if text.rstrip().endswith((".", "!", "?")) and len(words) > 8:
        score -= 0.2
    # Ingredient lines are short and rarely contain sentence punctuation.
    if len(words) <= 8:
        score += 0.1

    return max(0.0, min(1.0, score))
