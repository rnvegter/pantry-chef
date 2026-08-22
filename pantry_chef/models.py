"""Domain objects shared by the parser, the database and the search."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class RecipeIngredient:
    """One ingredient as it appears in one recipe."""

    canonical: str
    display: str
    raw: str = ""
    quantity: float | None = None
    unit: str | None = None
    note: str = ""
    is_staple: bool = False
    position: int = 0


@dataclass(slots=True)
class Recipe:
    """A recipe recovered from a book."""

    title: str
    ingredients: list[RecipeIngredient] = field(default_factory=list)
    instructions: str = ""
    section: str = ""
    servings: str = ""
    meals: list[str] = field(default_factory=list)
    cuisine: str = ""
    diets: list[str] = field(default_factory=list)
    allergens: list[str] = field(default_factory=list)
    diet_caveats: str = ""
    n_unknown: int = 0
    total_minutes: int | None = None
    active_minutes: int | None = None
    time_source: str = "unknown"
    has_long_wait: bool = False
    image_ref: str = ""      # where the photo lives inside the source book
    page: int = 0
    order_in_book: int = 0
    confidence: float = 0.0
    book_id: int | None = None
    id: int | None = None

    @property
    def core_ingredients(self) -> list[RecipeIngredient]:
        """Ingredients a cook actually has to shop for."""
        return [i for i in self.ingredients if not i.is_staple]

    @property
    def ingredient_names(self) -> list[str]:
        return [i.canonical for i in self.ingredients]


@dataclass(slots=True)
class Book:
    """One source file in the library."""

    path: str
    title: str = ""
    author: str = ""
    format: str = ""
    sha256: str = ""
    size_bytes: int = 0
    n_recipes: int = 0
    status: str = "pending"     # pending | indexed | failed | skipped
    error: str = ""
    id: int | None = None


# Leading step numbers the book already wrote: "1.", "2)", "Step 3:".
_STEP_NUMBER_RE = re.compile(r"^\s*(?:step\s*)?\d{1,2}\s*[.):\-]\s+", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def split_steps(instructions: str, max_steps: int = 40) -> list[str]:
    """Break a method into numbered steps.

    Books are inconsistent: some give one paragraph per step, some run the
    whole method together as prose. Line breaks are trusted where they exist;
    a single long blob is split on sentences instead, and any numbering the
    book already applied is removed so it is not doubled up.
    """
    if not instructions or not instructions.strip():
        return []

    lines = [line.strip() for line in instructions.split("\n") if line.strip()]

    # One long blob: fall back to sentences, grouped into readable steps.
    if len(lines) <= 1 and instructions.count(".") > 3:
        sentences = [s.strip() for s in _SENTENCE_RE.split(instructions) if s.strip()]
        lines, buffer = [], ""
        for sentence in sentences:
            buffer = f"{buffer} {sentence}".strip()
            # Group short sentences so a step is an action, not a fragment.
            if len(buffer) > 80:
                lines.append(buffer)
                buffer = ""
        if buffer:
            lines.append(buffer)

    steps = [_STEP_NUMBER_RE.sub("", line).strip() for line in lines]
    steps = _strip_bare_numbering(steps)
    return [s for s in steps if s][:max_steps]


_BARE_NUMBER_RE = re.compile(r"^(\d{1,2})\s+(?=[A-Za-z])")


def _strip_bare_numbering(steps: list[str]) -> list[str]:
    """Remove step numbers written with no punctuation after them.

    Some books set steps as "1 Preheat the oven", which the delimiter-based
    strip above cannot see. Removing every leading number would damage a step
    that legitimately opens with one ("200 g of the mixture..."), so this only
    fires when the numbers actually run in sequence like a numbered list.
    """
    numbered = [(i, _BARE_NUMBER_RE.match(step)) for i, step in enumerate(steps)]
    found = [(i, int(m.group(1))) for i, m in numbered if m]
    if len(found) < 2:
        return steps

    # The numbers must ascend, and start near the top of the list.
    values = [value for _i, value in found]
    if values[0] > 2 or values != sorted(values) or len(set(values)) != len(values):
        return steps

    stripped = list(steps)
    for index, _value in found:
        stripped[index] = _BARE_NUMBER_RE.sub("", stripped[index]).strip()
    return stripped


# Words that stay lowercase inside a title, unless they open or close it.
# Initialisms worth preserving. A length rule cannot work here: in a title
# that is entirely uppercase, every word looks like an initialism.
_TITLE_INITIALISMS = frozenset({
    "BBQ", "BLT", "PB", "PBJ", "NYC", "LA", "UK", "US", "USA", "DIY", "IPA",
    "MSG", "XL", "TV", "AM", "PM", "OK",
})

_TITLE_MINOR = frozenset({
    "a", "an", "the", "and", "or", "but", "nor", "of", "in", "on", "at", "to",
    "for", "from", "by", "with", "over", "into", "onto", "up", "as", "per",
    "vs", "via", "alla", "au", "aux", "de", "del", "della", "en", "et", "la",
    "le", "les", "con", "e", "y",
})


def display_title(title: str) -> str:
    """Present a title readably, taming the casing cookbooks set headings in.

    Print headings are often styled entirely in capitals, and occasionally
    entirely in lower case; both look wrong once the styling is gone, and
    unreadable in a script face. Either extreme is recased. A title that
    already uses mixed case is left exactly as the book wrote it.
    """
    if not title:
        return title

    letters = [c for c in title if c.isalpha()]
    if not letters:
        return title
    if len(letters) < 4:
        return title
    upper_share = sum(1 for c in letters if c.isupper()) / len(letters)
    if 0 < upper_share < 0.7:
        return title

    words = title.split()
    out: list[str] = []
    for index, word in enumerate(words):
        lowered = word.lower()
        # Keep an initialism that survived as its own word ("BBQ", "NYC").
        core = "".join(c for c in word if c.isalpha())
        if core.upper() in _TITLE_INITIALISMS:
            out.append(word.upper())
            continue
        if lowered in _TITLE_MINOR and 0 < index < len(words) - 1:
            out.append(lowered)
            continue
        # Capitalise the first letter, keep the rest lowercase, and handle
        # possessives and hyphenated compounds ("MOM'S" -> "Mom's").
        # Only the first alphabetic run is capitalised, so a possessive stays
        # lowercase: "MOM'S" becomes "Mom's", never "Mom'S".
        parts = _split_keep(lowered)
        rebuilt = ""
        seen_alpha = False
        for part in parts:
            if part.isalpha() and not seen_alpha:
                rebuilt += part.capitalize()
                seen_alpha = True
            else:
                rebuilt += part
        out.append(rebuilt)
    return " ".join(out)


def _split_keep(word: str) -> list[str]:
    """Split a word into alphabetic runs and the punctuation between them."""
    parts: list[str] = []
    buffer = ""
    alpha = None
    for char in word:
        if alpha is None or char.isalpha() == alpha:
            buffer += char
        else:
            parts.append(buffer)
            buffer = char
        alpha = char.isalpha()
    if buffer:
        parts.append(buffer)
    return parts
