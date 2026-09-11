"""Corrections made to a recipe by hand.

Extraction is heuristic, and it gets things wrong: a roast timed at three
minutes, a method step filed as an ingredient, a title that is really the
chapter heading. Those errors flow into everything downstream — the time
filter, the allergen check, the pantry match — so a correction has to reach
all of it, not just the page.

A correction is therefore applied by rewriting the recipe in place, through
the same steps indexing uses: the ingredient lines are parsed and the recipe
is re-classified exactly as the book's own text would be. The recipe keeps its
id, so links to it keep working.

The correction itself is stored apart, keyed like a favourite ("the Nth recipe
called X in book Y"), because a --force re-index deletes and re-creates every
recipe. After a re-read, the stored corrections are applied again. Only the
fields actually changed are stored, so a better parser still improves the
rest; and the book's own version is kept alongside, so any correction can be
undone without re-reading the book.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import db
from .models import Recipe, split_steps
from .parse.segment import enrich, ingredients_from_lines
from .parse.timing import extract_time

EDITABLE = ("title", "servings", "total_minutes", "ingredients", "instructions")

# Limits generous enough for any real recipe, and small enough that a stray
# paste cannot bloat the index.
MAX_TITLE = 200
MAX_SERVINGS = 60
MAX_MINUTES = 7 * 24 * 60
MAX_LINES = 200
MAX_LINE = 500
MAX_METHOD = 50_000

# Where a time came from when it was estimated rather than printed. Editing
# the method of such a recipe re-estimates it; a printed time is left alone.
_ESTIMATED = ("method", "estimate", "unknown")


class EditError(ValueError):
    """A correction that cannot be accepted, with a reason fit to show."""


@dataclass(slots=True)
class Saved:
    changed: list[str]            # fields that now differ from the book
    folded: list[str]             # lines that did not become an ingredient of their own


def _steps(text: str | None) -> list[str]:
    # The page shows at most 40 steps; the editor must never drop the rest.
    return split_steps(text or "", max_steps=100_000)


# --- reading ------------------------------------------------------------------

def current_values(conn: sqlite3.Connection, recipe_id: int) -> dict[str, Any] | None:
    """The editable fields as the recipe stands now, plus its time's origin."""
    row = conn.execute(
        """SELECT title, servings, total_minutes, active_minutes, time_source,
                  instructions FROM recipes WHERE id = ?""",
        (recipe_id,),
    ).fetchone()
    if row is None:
        return None
    lines: list[str] = []
    seen: set[str] = set()
    for item in conn.execute(
        "SELECT raw, display FROM recipe_ingredients WHERE recipe_id = ? ORDER BY position",
        (recipe_id,),
    ):
        # A line naming two ingredients ("salt and pepper") is stored once per
        # ingredient; it is still one line to the cook.
        line = (item["raw"] or item["display"] or "").strip()
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    return {
        "title": row["title"],
        "servings": row["servings"],
        "total_minutes": row["total_minutes"],
        "ingredients": lines,
        "instructions": row["instructions"],
        "active_minutes": row["active_minutes"],
        "time_source": row["time_source"],
    }


def _stored(conn: sqlite3.Connection,
            key: tuple[str, str, int]) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    if not db.has_table(conn, "recipe_edits"):
        return None
    row = conn.execute(
        """SELECT fields, original, edited_at FROM recipe_edits
           WHERE book_path = ? AND title_key = ? AND occurrence = ?""",
        key,
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["fields"]), json.loads(row["original"]), row["edited_at"]


def _key(conn: sqlite3.Connection, recipe_id: int) -> tuple[str, str, int] | None:
    found = db.recipe_key(conn, recipe_id)
    return found[:3] if found else None


def editor(conn: sqlite3.Connection, recipe_id: int) -> dict[str, Any] | None:
    """Everything the edit form needs: the values now, the book's, what differs."""
    values = current_values(conn, recipe_id)
    key = _key(conn, recipe_id)
    if values is None or key is None:
        return None
    stored = _stored(conn, key)
    fields, original, edited_at = stored if stored else ({}, values, None)
    shown = {f: values[f] for f in EDITABLE}
    # Your ingredient lines exactly as typed — including any the page folded
    # into an ingredient already listed, which the recipe itself no longer shows.
    if "ingredients" in fields:
        shown["ingredients"] = fields["ingredients"]
    book = {f: original[f] for f in EDITABLE}
    # One step per paragraph is how the form presents a method. It compares by
    # steps, so presenting it differently is not itself a change.
    for side in (shown, book):
        side["instructions"] = "\n\n".join(_steps(side["instructions"]))
    return {"values": shown, "book": book, "changed": sorted(fields), "edited_at": edited_at}


def state(conn: sqlite3.Connection, recipe_id: int) -> dict[str, Any]:
    """Whether a recipe has been corrected, and which fields — for the page."""
    key = _key(conn, recipe_id)
    stored = _stored(conn, key) if key else None
    if not stored:
        return {"edited": False, "fields": [], "edited_at": None}
    return {"edited": True, "fields": sorted(stored[0]), "edited_at": stored[2]}


def edited_ids(conn: sqlite3.Connection) -> set[int]:
    """Current ids of every corrected recipe that still resolves."""
    if not db.has_table(conn, "recipe_edits"):
        return set()
    return {int(row[0]) for row in conn.execute(db.keyed_ids_sql(conn, "recipe_edits"))}


def summary(conn: sqlite3.Connection) -> dict[str, int]:
    """How many corrections there are, and how many no longer find their recipe."""
    if not db.has_table(conn, "recipe_edits"):
        return {"total": 0, "available": 0, "missing": 0}
    stored = int(conn.execute("SELECT COUNT(*) FROM recipe_edits").fetchone()[0])
    resolved = len(edited_ids(conn))
    return {"total": stored, "available": resolved, "missing": max(0, stored - resolved)}


# --- validating -------------------------------------------------------------------

def clean(changes: dict[str, Any]) -> dict[str, Any]:
    """Check and tidy submitted fields. Raises EditError with a readable reason."""
    out: dict[str, Any] = {}
    for field, value in changes.items():
        if field not in EDITABLE:
            raise EditError(f"“{field}” is not something that can be edited")
        if field == "title":
            text = str(value or "").strip()
            if not text:
                raise EditError("A recipe needs a title")
            if len(text) > MAX_TITLE:
                raise EditError(f"The title is longer than {MAX_TITLE} characters")
            out[field] = text
        elif field == "servings":
            text = str(value or "").strip()
            if len(text) > MAX_SERVINGS:
                raise EditError(f"Servings is longer than {MAX_SERVINGS} characters")
            out[field] = text
        elif field == "total_minutes":
            if value in (None, ""):
                out[field] = None
                continue
            try:
                minutes = int(value)
            except (TypeError, ValueError):
                raise EditError("The time must be a whole number of minutes") from None
            if not 1 <= minutes <= MAX_MINUTES:
                raise EditError(f"The time must be between 1 and {MAX_MINUTES} minutes")
            out[field] = minutes
        elif field == "ingredients":
            if isinstance(value, str):
                value = value.splitlines()
            lines = [str(line).strip() for line in (value or []) if str(line).strip()]
            if not lines:
                raise EditError("A recipe needs at least one ingredient")
            if len(lines) > MAX_LINES:
                raise EditError(f"More than {MAX_LINES} ingredient lines")
            if any(len(line) > MAX_LINE for line in lines):
                raise EditError(f"An ingredient line is longer than {MAX_LINE} characters")
            out[field] = lines
        elif field == "instructions":
            text = str(value or "").strip()
            if len(text) > MAX_METHOD:
                raise EditError(f"The method is longer than {MAX_METHOD} characters")
            out[field] = text
    return out


def _normal(field: str, value: Any) -> Any:
    """A field reduced to what matters, so an untouched form saves nothing.

    The form shows the method one step per paragraph, which is not how the
    book's text is laid out; comparing the steps rather than the characters
    means re-saving it unchanged is not mistaken for a correction.
    """
    if field == "ingredients":
        return [str(line).strip() for line in (value or []) if str(line).strip()]
    if field == "instructions":
        return _steps(value)
    if field == "total_minutes":
        return int(value) if value not in (None, "") else None
    return str(value or "").strip()


# --- writing ------------------------------------------------------------------------

def _rewrite(conn: sqlite3.Connection, recipe_id: int, original: dict[str, Any],
             overrides: dict[str, Any]) -> list[str]:
    """Rewrite a recipe as the book's version with `overrides` laid over it.

    Returns the ingredient lines that did not become an ingredient of their
    own: a second mention of something already listed, or a line that names
    no ingredient at all ("For the sauce:").
    """
    row = conn.execute(
        """SELECT r.section, r.book_id, b.title AS book_title
           FROM recipes r JOIN books b ON b.id = r.book_id WHERE r.id = ?""",
        (recipe_id,),
    ).fetchone()
    values = {**original, **overrides}
    lines = list(values["ingredients"])
    ingredients = ingredients_from_lines(lines)

    if "total_minutes" in overrides:
        total = overrides["total_minutes"]
        active = original["active_minutes"]
        active = active if active and total and active <= total else None
        source = "edited" if total else "unknown"
    elif "instructions" in overrides and original["time_source"] in _ESTIMATED:
        timing = extract_time("", values["instructions"])
        total, active, source = timing.total_minutes, timing.active_minutes, timing.source
    else:
        total = original["total_minutes"]
        active = original["active_minutes"]
        source = original["time_source"]

    recipe = Recipe(
        title=values["title"],
        ingredients=ingredients,
        instructions=values["instructions"],
        section=row["section"],
        servings=values["servings"],
        total_minutes=total,
        active_minutes=active,
        time_source=source,
    )
    enrich(recipe, row["book_title"])

    before = {r[0] for r in conn.execute(
        "SELECT ingredient_id FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))}
    core = [i for i in ingredients if not i.is_staple]
    conn.execute(
        """UPDATE recipes SET title = ?, servings = ?, instructions = ?,
               total_minutes = ?, active_minutes = ?, time_source = ?,
               n_ingredients = ?, n_core = ?, n_unknown = ?, diet_caveats = ?
           WHERE id = ?""",
        (recipe.title, recipe.servings, recipe.instructions, total, active, source,
         len(ingredients), len(core), recipe.n_unknown, recipe.diet_caveats, recipe_id),
    )
    db.write_recipe_children(conn, recipe_id, recipe)

    # Keep the "used in N recipes" counts behind ingredient suggestions honest,
    # for just the ingredients this touched.
    after = {r[0] for r in conn.execute(
        "SELECT ingredient_id FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))}
    touched = sorted(before | after)
    if touched:
        marks = ",".join("?" * len(touched))
        conn.execute(
            f"""UPDATE ingredients SET n_recipes = (
                    SELECT COUNT(*) FROM recipe_ingredients ri
                    WHERE ri.ingredient_id = ingredients.id)
                WHERE id IN ({marks})""",
            touched,
        )

    kept = {i.raw for i in ingredients}
    return [line for line in lines if line not in kept]


def save(conn: sqlite3.Connection, recipe_id: int, changes: dict[str, Any]) -> Saved | None:
    """Apply a correction. Returns None if there is no such recipe.

    Fields set back to the book's value stop being corrections; a recipe with
    none left is simply the book's recipe again.
    """
    cleaned = clean(changes)
    key = _key(conn, recipe_id)
    current = current_values(conn, recipe_id)
    if key is None or current is None:
        return None

    stored = _stored(conn, key)
    overrides, original = (dict(stored[0]), stored[1]) if stored else ({}, current)
    for field, value in cleaned.items():
        if _normal(field, value) == _normal(field, original[field]):
            overrides.pop(field, None)
        else:
            overrides[field] = value

    folded = _rewrite(conn, recipe_id, original, overrides)
    if overrides:
        conn.execute(
            """INSERT INTO recipe_edits (book_path, title_key, occurrence, fields, original)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(book_path, title_key, occurrence) DO UPDATE SET
                   fields = excluded.fields, edited_at = datetime('now')""",
            (*key, json.dumps(overrides), json.dumps(original)),
        )
    elif stored:
        conn.execute(
            "DELETE FROM recipe_edits WHERE book_path = ? AND title_key = ? AND occurrence = ?",
            key,
        )
    conn.commit()
    return Saved(changed=sorted(overrides), folded=folded if "ingredients" in overrides else [])


def revert(conn: sqlite3.Connection, recipe_id: int) -> bool:
    """Put the book's version back. False if there is no such recipe."""
    key = _key(conn, recipe_id)
    if key is None:
        return False
    stored = _stored(conn, key)
    if stored:
        save(conn, recipe_id, {f: stored[1][f] for f in EDITABLE})
    return True


def reapply_book(conn: sqlite3.Connection, book_id: int) -> int:
    """Lay stored corrections over a book's freshly re-read recipes.

    Called inside the indexing transaction, after the book's recipes have been
    written again. The re-read is the book's version now, so it replaces the
    stored original: a parser fix to a field nobody corrected shows through.
    Returns how many corrections found their recipe.
    """
    if not db.has_table(conn, "recipe_edits"):
        return 0
    book = conn.execute("SELECT path FROM books WHERE id = ?", (book_id,)).fetchone()
    if book is None:
        return 0
    stored = conn.execute(
        "SELECT title_key, occurrence, fields FROM recipe_edits WHERE book_path = ?",
        (book["path"],),
    ).fetchall()
    if not stored:
        return 0

    title_key = db.title_key_sql(conn)
    ranked = {
        (row["title_key"], int(row["occurrence"])): int(row["id"])
        for row in conn.execute(
            f"""SELECT r.id AS id, {title_key} AS title_key,
                       ROW_NUMBER() OVER (PARTITION BY {title_key}
                                          ORDER BY r.order_in_book, r.id) AS occurrence
                FROM recipes r WHERE r.book_id = ?""",
            (book_id,),
        )
    }
    applied = 0
    for edit in stored:
        recipe_id = ranked.get((edit["title_key"], int(edit["occurrence"])))
        if recipe_id is None:
            continue            # kept: it comes back if the recipe does
        original = current_values(conn, recipe_id)
        if original is None:     # pragma: no cover — the id was just ranked
            continue
        _rewrite(conn, recipe_id, original, json.loads(edit["fields"]))
        conn.execute(
            """UPDATE recipe_edits SET original = ?
               WHERE book_path = ? AND title_key = ? AND occurrence = ?""",
            (json.dumps(original), book["path"], edit["title_key"], edit["occurrence"]),
        )
        applied += 1
    return applied
