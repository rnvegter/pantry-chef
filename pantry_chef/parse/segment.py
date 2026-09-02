"""Split a book's blocks into individual recipes.

Cookbooks are wildly inconsistent, so we do not try to recognise a recipe from
its title -- titles are unconstrained prose. Instead we anchor on the one thing
every recipe has and nothing else does: a run of consecutive ingredient lines.

Having found a run, the title is the nearest heading above it, the metadata sits
between the two, and the method is whatever follows until the next recipe.
"""

from __future__ import annotations

import re

from ..extract.blocks import HEADING, IMAGE, LIST_ITEM, Block
from ..models import Recipe, RecipeIngredient
from .classify import classify
from .diet import profile as diet_profile
from .ingredients import ingredient_score, is_title_case, parse_ingredient_line
from .lexicon import (
    COOKING_VERBS,
    FRONT_BACK_MATTER,
    INGREDIENT_HEADERS,
    METHOD_HEADERS,
)
from .timing import extract_time

# Tuning constants. Deliberately conservative: a missed recipe costs one recipe,
# a false positive pollutes every search.
MIN_RUN = 3               # fewest ingredient lines that can form a recipe
MAX_RUN = 80              # longer than this is an index or shopping list
MAX_GAP = 2               # non-ingredient lines tolerated inside a run
TITLE_LOOKBACK = 14       # blocks to search above a run for its title
MAX_INSTRUCTION_BLOCKS = 80
INGREDIENT_THRESHOLD = 0.5
MIN_CONFIDENCE = 0.4      # below this a candidate is more likely an index page

# Lines that carry the recipe's numbers rather than its name.
_METADATA_RE = re.compile(
    r"\b(?:serves|servings|makes|yield|feeds|prep(?:aration)?|cook(?:ing)?|"
    r"total|ready in|hands[- ]on|active|bake|baking|chill|rest)\b"
    r"[^a-z]{0,4}(?:\d|time)",
    re.IGNORECASE,
)

# Only a count, a range, and a unit we actually recognise. An open-ended
# trailing word swallows whatever heading follows ("Serves 6 CINNAMON").
_SERVINGS_UNITS = (
    "servings?|portions?|people|burgers?|cookies?|pieces?|slices?|bars?|"
    "muffins?|cupcakes?|quesadillas?|pizzas?|loaves|loaf|jars?|cakes?|"
    "sandwiches|tacos?|rolls?|scones?|pancakes?|waffles?|dozen"
)
_SERVINGS_RE = re.compile(
    r"\b(?:serves|servings?|makes|yields?|feeds)\b[:\s]*"
    rf"(\d+(?:\s*(?:to|or|[-–])\s*\d+)?(?:\s+(?:{_SERVINGS_UNITS}))?)",
    re.IGNORECASE,
)


def _is_ingredient(block: Block) -> bool:
    """Whether a block reads as an ingredient line."""
    if block.kind == IMAGE or block.words > 22:
        return False
    # A heading is the recipe's name or a section label, never an ingredient --
    # and recipe names are built from food words, so without this rule the
    # title gets absorbed into the ingredient list below it.
    if block.is_heading:
        return False
    score = ingredient_score(block.text)
    # Explicit list markup is corroborating evidence, so accept a weaker score.
    if block.kind == LIST_ITEM:
        return score >= INGREDIENT_THRESHOLD - 0.15
    return score >= INGREDIENT_THRESHOLD


def _normalise_heading(text: str) -> str:
    return text.strip().strip(".:—–-").lower()


def _is_structural_heading(text: str) -> bool:
    """Headings that label a part of a recipe rather than name one."""
    norm = _normalise_heading(text)
    return (
        norm in INGREDIENT_HEADERS
        or norm in METHOD_HEADERS
        or norm in FRONT_BACK_MATTER
        or norm.startswith("for the")
        or len(norm) < 2
    )


# "MAKES ABOUT 3 CUPS", "Serves 4 to 6" — a yield line sits exactly where a
# title does, and is often set in the same capitals, so it gets picked up as
# one. It is metadata about the recipe, never its name.
_YIELD_LINE_RE = re.compile(
    r"^\s*(?:makes|serves|serving|servings|yields?|feeds)\b", re.IGNORECASE)


def _looks_like_title(block: Block) -> bool:
    """Whether a block can serve as a recipe title."""
    if block.kind == IMAGE:
        return False
    text = block.text.strip()
    if _YIELD_LINE_RE.match(text):
        return False
    if not (2 <= len(text) <= 120):
        return False
    if _is_structural_heading(text):
        return False
    words = text.split()
    if len(words) > 14:
        return False
    # A sentence is prose, not a title.
    if text.endswith((".", "!", "?")) and len(words) > 4:
        return False
    if words[0].lower().rstrip(",.") in COOKING_VERBS and not block.is_heading:
        return False
    return True


def find_ingredient_runs(blocks: list[Block]) -> list[tuple[int, int]]:
    """Locate maximal runs of ingredient lines as (start, end) index pairs."""
    flags = [_is_ingredient(b) for b in blocks]
    runs: list[tuple[int, int]] = []

    i = 0
    while i < len(flags):
        if not flags[i]:
            i += 1
            continue

        start = i
        end = i
        gap = 0
        j = i + 1
        while j < len(flags) and (end - start) < MAX_RUN:
            if flags[j]:
                end = j
                gap = 0
            else:
                gap += 1
                if gap > MAX_GAP:
                    break
            j += 1

        count = sum(1 for k in range(start, end + 1) if flags[k])
        if count >= MIN_RUN:
            runs.append((start, end))
        i = end + 1

    return runs


def _is_metadata_line(text: str) -> bool:
    """Whether a line states servings or timings rather than naming a dish."""
    return bool(_METADATA_RE.search(text))


def _title_fit(block: Block, distance: int) -> float:
    """Score a block's suitability as the title of the run below it.

    Nearness matters, but so does shape: a title-cased heading beats a bare
    chapter heading further up, which is why this is scored rather than simply
    taking the first heading found.
    """
    if not _looks_like_title(block) or _is_metadata_line(block.text):
        return -1.0

    score = 1.0
    if block.is_heading:
        score += 2.0
        # h1 is usually the chapter; h2/h3 is usually the recipe.
        score += 1.0 if block.level in (2, 3) else 0.0
    if is_title_case(block.text):
        score += 2.0
    if block.bold:
        score += 0.5
    return score - 0.2 * distance


def _find_title(blocks: list[Block], run_start: int, floor: int) -> tuple[int, str]:
    """Search upwards from an ingredient run for the recipe's title."""
    lower = max(floor, run_start - TITLE_LOOKBACK)

    best_index, best_score = run_start, 0.0
    for i in range(run_start - 1, lower - 1, -1):
        block = blocks[i]
        if _is_ingredient(block):
            continue
        score = _title_fit(block, run_start - 1 - i)
        if score > best_score:
            best_index, best_score = i, score

    if best_score <= 0.0:
        return run_start, ""
    return best_index, blocks[best_index].text.strip()


def _collect_instructions(blocks: list[Block], start: int, stop: int) -> str:
    """Gather the method text between the ingredient run and the next recipe."""
    parts: list[str] = []
    for i in range(start, min(stop, start + MAX_INSTRUCTION_BLOCKS)):
        block = blocks[i]
        text = block.text.strip()
        if not text:
            continue
        if block.is_heading and _normalise_heading(text) in METHOD_HEADERS:
            continue
        if block.is_heading and block.level <= 2 and parts:
            break       # a new top-level heading means a new recipe
        parts.append(text)
    return "\n".join(parts).strip()


def _find_image(blocks: list[Block], title_index: int, floor: int, ceiling: int) -> str:
    """The photograph belonging to this recipe, if the book has one.

    Cookbooks place the photo either side of the title -- facing page, or
    directly above the method -- so the search runs outwards from the title and
    takes the nearest image inside the recipe's own span.
    """
    best = ""
    best_distance = 10**9
    for i in range(max(0, floor), min(len(blocks), ceiling)):
        block = blocks[i]
        if block.kind != IMAGE:
            continue
        distance = abs(i - title_index)
        if distance < best_distance:
            best, best_distance = block.text, distance
    return best


def _section_for(blocks: list[Block], index: int) -> str:
    """The nearest chapter heading above a recipe, e.g. 'Soups'."""
    for i in range(index - 1, max(-1, index - 400), -1):
        block = blocks[i]
        if block.is_heading and block.level == 1 and not _is_structural_heading(block.text):
            return block.text.strip()
    return ""


def _confidence(recipe: Recipe, had_heading: bool, run_len: int) -> float:
    """How much to trust this extraction, in 0..1."""
    score = 0.25
    if had_heading:
        score += 0.25
    if recipe.title:
        score += 0.1
    if run_len >= 5:
        score += 0.15
    if recipe.instructions and len(recipe.instructions.split()) >= 25:
        score += 0.15
    # A real method gives orders; an index or menu does not.
    first_words = [
        line.strip().split()[0].lower().rstrip(",.")
        for line in recipe.instructions.split("\n") if line.strip()
    ]
    if not any(w in COOKING_VERBS for w in first_words):
        score -= 0.3
    if recipe.time_source in {"label", "labels-summed"}:
        score += 0.15
    elif recipe.time_source == "method":
        score += 0.05
    if recipe.servings:
        score += 0.05
    if len(recipe.core_ingredients) < 2:
        score -= 0.25
    return max(0.0, min(1.0, score))


def find_recipes(blocks: list[Block], min_confidence: float = MIN_CONFIDENCE,
                 book_title: str = "") -> list[Recipe]:
    """Extract every recipe we can find in a book's blocks."""
    runs = find_ingredient_runs(blocks)
    if not runs:
        return []

    recipes: list[Recipe] = []
    previous_end = 0

    for index, (run_start, run_end) in enumerate(runs):
        title_index, title = _find_title(blocks, run_start, previous_end)
        had_heading = title_index < run_start and blocks[title_index].is_heading

        # The method runs until the next recipe's title.
        if index + 1 < len(runs):
            next_title_index, _ = _find_title(blocks, runs[index + 1][0], run_end + 1)
            stop = min(next_title_index, runs[index + 1][0])
        else:
            stop = len(blocks)

        header_text = " ".join(
            blocks[i].text for i in range(title_index, run_start)
        )
        instructions = _collect_instructions(blocks, run_end + 1, stop)

        ingredients: list[RecipeIngredient] = []
        seen: set[str] = set()
        for i in range(run_start, run_end + 1):
            block = blocks[i]
            if not _is_ingredient(block):
                continue
            for parsed in parse_ingredient_line(block.text):
                if parsed.canonical in seen:
                    continue
                seen.add(parsed.canonical)
                ingredients.append(
                    RecipeIngredient(
                        canonical=parsed.canonical,
                        display=parsed.display,
                        raw=parsed.raw,
                        quantity=parsed.quantity,
                        unit=parsed.unit,
                        note=parsed.note,
                        is_staple=parsed.is_staple,
                        position=len(ingredients),
                    )
                )

        if len(ingredients) < MIN_RUN:
            continue
        # A list with no method is a menu or a shopping list, not a recipe.
        if not instructions and len(ingredients) < 5:
            continue

        timing = extract_time(header_text + "\n" + title, instructions)
        servings_match = _SERVINGS_RE.search(header_text) or _SERVINGS_RE.search(title)

        image_ref = _find_image(
            blocks, title_index, floor=previous_end, ceiling=stop)

        recipe = Recipe(
            title=title or "Untitled recipe",
            ingredients=ingredients,
            instructions=instructions,
            section=_section_for(blocks, title_index),
            servings=servings_match.group(1).strip() if servings_match else "",
            total_minutes=timing.total_minutes,
            active_minutes=timing.active_minutes,
            time_source=timing.source,
            has_long_wait=timing.has_long_wait,
            image_ref=image_ref,
            page=blocks[run_start].doc,
            order_in_book=len(recipes),
        )
        judged = classify(
            title=recipe.title,
            section=recipe.section,
            ingredients=[i.canonical for i in ingredients],
            instructions=instructions,
            book_title=book_title,
        )
        recipe.meals = judged.meals
        recipe.cuisine = judged.cuisine or ""

        diet = diet_profile([i.canonical for i in ingredients])
        recipe.diets = sorted(diet.diets)
        recipe.allergens = sorted(diet.allergens)
        recipe.n_unknown = diet.n_unknown
        recipe.diet_caveats = " · ".join(diet.caveats)

        recipe.confidence = _confidence(recipe, had_heading, len(ingredients))
        if recipe.confidence < min_confidence:
            continue
        recipes.append(recipe)
        previous_end = run_end + 1

    return recipes
