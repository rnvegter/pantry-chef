"""Correcting a recipe by hand.

What these pin down: a correction reaches search, filters and allergens, not
just the page; it survives a re-read of the book; an untouched form changes
nothing; and undoing it gives back exactly the book's recipe.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

import make_fixtures

from pantry_chef import edits
from pantry_chef.db import add_favourite, connect, favourite_ids
from pantry_chef.index import ingest
from pantry_chef.search import Query, get_recipe, search

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    make_fixtures.build_all(FIXTURES)
    path = tmp_path_factory.mktemp("edits") / "library.db"
    ingest([FIXTURES], path, workers=1, force=True)
    return path


@pytest.fixture
def db(library, tmp_path):
    target = tmp_path / "edits.db"
    shutil.copy(library, target)
    conn = connect(target)
    yield conn
    conn.close()


def _linguine(conn):
    return conn.execute(
        "SELECT id FROM recipes WHERE title LIKE '%Linguine%' ORDER BY id LIMIT 1").fetchone()[0]


def _snapshot(conn, recipe_id):
    """Everything a recipe is made of, for before-and-after comparisons."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(recipes)")]
    return (
        conn.execute(f"SELECT {','.join(cols)} FROM recipes WHERE id = ?", (recipe_id,)).fetchall(),
        conn.execute("SELECT ingredient_id, display, raw, quantity, unit, note, is_staple, position"
                     " FROM recipe_ingredients WHERE recipe_id = ? ORDER BY position",
                     (recipe_id,)).fetchall(),
        conn.execute("SELECT kind, value FROM recipe_tags WHERE recipe_id = ? ORDER BY 1, 2",
                     (recipe_id,)).fetchall(),
    )


# --- the correction reaches everything ---------------------------------------------

def test_a_corrected_title_is_searched(db):
    recipe_id = _linguine(db)
    edits.save(db, recipe_id, {"title": "Weeknight Tomato Spaghetti"})

    found, _ = search(db, Query(title="weeknight spaghetti"))
    assert [r.recipe_id for r in found] == [recipe_id]
    assert get_recipe(db, recipe_id).title == "Weeknight Tomato Spaghetti"


def test_a_corrected_time_moves_the_time_filter(db):
    recipe_id = _linguine(db)
    # The fixtures hold the dish in three editions and search shows one, so
    # the search is kept to this recipe's own book.
    book = db.execute("SELECT b.title FROM recipes r JOIN books b ON b.id = r.book_id"
                      " WHERE r.id = ?", (recipe_id,)).fetchone()[0]
    quick = Query(max_minutes=30, book=book, limit=200)
    assert recipe_id in {r.recipe_id for r in search(db, quick)[0]}

    edits.save(db, recipe_id, {"total_minutes": 95})
    assert recipe_id not in {r.recipe_id for r in search(db, quick)[0]}
    recipe = get_recipe(db, recipe_id)
    assert recipe.total_minutes == 95 and recipe.time_source == "edited"
    # A time you corrected counts as known, like one the book printed.
    assert recipe_id in {r.recipe_id for r in search(
        db, Query(require_timed=True, book=book, limit=200))[0]}


def test_corrected_ingredients_reach_allergens_and_the_pantry_match(db):
    recipe_id = _linguine(db)
    lines = edits.current_values(db, recipe_id)["ingredients"]
    assert "peanuts" not in get_recipe(db, recipe_id).allergens

    edits.save(db, recipe_id, {"ingredients": [*lines, "2 tablespoons peanut butter"]})

    recipe = get_recipe(db, recipe_id)
    assert "peanuts" in recipe.allergens
    assert recipe_id not in {r.recipe_id for r in search(
        db, Query(free_from=["peanuts"], limit=200))[0]}
    assert any(i.canonical == "peanut butter" for i in recipe.ingredients)


def test_the_recipe_keeps_its_id(db):
    recipe_id = _linguine(db)
    edits.save(db, recipe_id, {"title": "Renamed", "total_minutes": 12})
    assert get_recipe(db, recipe_id) is not None


# --- what counts as a change ---------------------------------------------------------

def test_saving_the_form_untouched_changes_nothing(db):
    recipe_id = _linguine(db)
    before = _snapshot(db, recipe_id)
    form = edits.editor(db, recipe_id)

    saved = edits.save(db, recipe_id, form["values"])
    assert saved.changed == []
    assert _snapshot(db, recipe_id) == before
    assert db.execute("SELECT COUNT(*) FROM recipe_edits").fetchone()[0] == 0


def test_the_method_is_compared_by_steps_not_characters(db):
    """The form shows one step per paragraph; the book's text is laid out
    differently. Re-saving it as shown is not a correction."""
    recipe_id = _linguine(db)
    shown = edits.editor(db, recipe_id)["values"]["instructions"]
    assert "\n\n" in shown
    assert edits.save(db, recipe_id, {"instructions": shown}).changed == []


def test_setting_a_field_back_to_the_book_drops_that_correction(db):
    recipe_id = _linguine(db)
    book_title = edits.current_values(db, recipe_id)["title"]
    edits.save(db, recipe_id, {"title": "Something else", "total_minutes": 50})

    assert edits.save(db, recipe_id, {"title": book_title}).changed == ["total_minutes"]
    assert edits.save(db, recipe_id, {"total_minutes": None}).changed == ["total_minutes"]
    book_time = edits.editor(db, recipe_id)["book"]["total_minutes"]
    assert edits.save(db, recipe_id, {"total_minutes": book_time}).changed == []
    assert db.execute("SELECT COUNT(*) FROM recipe_edits").fetchone()[0] == 0


def test_undo_gives_back_exactly_the_books_recipe(db):
    recipe_id = _linguine(db)
    before = _snapshot(db, recipe_id)
    lines = edits.current_values(db, recipe_id)["ingredients"]
    edits.save(db, recipe_id, {
        "title": "Different", "servings": "12", "total_minutes": 240,
        "ingredients": [*lines[1:], "2 tablespoons peanut butter"],
        "instructions": "Just one step now.",
    })
    assert _snapshot(db, recipe_id) != before

    assert edits.revert(db, recipe_id)
    assert _snapshot(db, recipe_id) == before
    assert edits.state(db, recipe_id)["edited"] is False


def test_lines_that_fold_into_another_ingredient_are_reported(db):
    recipe_id = _linguine(db)
    lines = edits.current_values(db, recipe_id)["ingredients"]
    repeat = f"{lines[0]}, extra"            # the same ingredient a second time
    saved = edits.save(db, recipe_id, {"ingredients": [*lines, repeat]})
    assert saved.folded == [repeat]
    # The line is kept in the correction, so the editor still shows it.
    assert repeat in edits.editor(db, recipe_id)["values"]["ingredients"]


def test_editing_the_method_re_estimates_an_estimated_time(db):
    recipe_id = _linguine(db)
    db.execute("UPDATE recipes SET time_source = 'method' WHERE id = ?", (recipe_id,))
    db.commit()
    edits.save(db, recipe_id, {"instructions": "Boil the pasta for 12 minutes.\n\nToss and serve."})
    assert get_recipe(db, recipe_id).total_minutes == 12


def test_a_long_method_is_not_cut_short_by_the_editor(db):
    """The page shows at most 40 steps; the editor must not lose the rest."""
    recipe_id = _linguine(db)
    method = "\n".join(f"Step number {n} of the long method." for n in range(1, 61))
    edits.save(db, recipe_id, {"instructions": method})
    shown = edits.editor(db, recipe_id)["values"]["instructions"]
    assert "Step number 60" in shown


@pytest.mark.parametrize("changes,message", [
    ({"title": "   "}, "needs a title"),
    ({"total_minutes": 0}, "between 1 and"),
    ({"total_minutes": "soon"}, "whole number"),
    ({"ingredients": ["", "  "]}, "at least one ingredient"),
    ({"page": 3}, "not something that can be edited"),
])
def test_unacceptable_corrections_say_why(db, changes, message):
    with pytest.raises(edits.EditError, match=message):
        edits.save(db, _linguine(db), changes)


def test_an_unknown_recipe_cannot_be_edited(db):
    assert edits.save(db, 999_999, {"title": "x"}) is None
    assert edits.revert(db, 999_999) is False
    assert edits.editor(db, 999_999) is None


# --- surviving a re-read ------------------------------------------------------------

def test_corrections_survive_a_forced_reindex(tmp_path):
    make_fixtures.build_all(FIXTURES)
    database = tmp_path / "reindex.db"
    ingest([FIXTURES], database, workers=1, force=True)

    conn = connect(database)
    before_id = _linguine(conn)
    book_time = get_recipe(conn, before_id).total_minutes
    edits.save(conn, before_id, {"title": "Corrected Linguine", "total_minutes": 33})
    conn.close()

    ingest([FIXTURES], database, workers=1, force=True)

    conn = connect(database)
    rows = conn.execute(
        "SELECT id, total_minutes, time_source FROM recipes WHERE title = 'Corrected Linguine'"
    ).fetchall()
    assert len(rows) == 1, "the correction should be laid over the re-read recipe"
    new_id, minutes, source = rows[0]
    assert new_id != before_id and minutes == 33 and source == "edited"
    # The book's version is refreshed from the re-read, so undo still works.
    assert edits.editor(conn, new_id)["book"]["total_minutes"] == book_time
    edits.revert(conn, new_id)
    assert get_recipe(conn, new_id).total_minutes == book_time
    conn.close()


def test_a_favourite_survives_its_title_being_corrected(db):
    """Favourites are keyed on the book's title, which a correction leaves alone."""
    recipe_id = _linguine(db)
    add_favourite(db, recipe_id)
    edits.save(db, recipe_id, {"title": "Totally New Name"})
    assert favourite_ids(db) == {recipe_id}
    edits.revert(db, recipe_id)
    assert favourite_ids(db) == {recipe_id}


def test_an_old_database_opened_read_only_still_finds_favourites(library, tmp_path):
    """Before source_title existed the shown title was the book's; a read-only
    connection that has not migrated falls back to it."""
    target = tmp_path / "old.db"
    shutil.copy(library, target)
    conn = connect(target)
    recipe_id = _linguine(conn)
    add_favourite(conn, recipe_id)
    conn.close()

    raw = sqlite3.connect(target)
    raw.execute("ALTER TABLE recipes DROP COLUMN source_title")
    raw.commit()
    raw.close()

    old = connect(target, read_only=True)
    assert favourite_ids(old) == {recipe_id}
    assert edits.state(old, recipe_id)["edited"] is False


# --- over HTTP ------------------------------------------------------------------

@pytest.fixture
def client(library, tmp_path, monkeypatch):
    target = tmp_path / "http.db"
    shutil.copy(library, target)
    monkeypatch.setenv("PANTRY_CHEF_DB", str(target))
    from pantry_chef.web.app import app
    with TestClient(app) as test_client:
        yield test_client


def _http_linguine(client):
    return client.post("/api/search", json={"title": "linguine"}).json()["results"][0]["id"]


def test_http_edit_round_trip(client):
    recipe_id = _http_linguine(client)
    form = client.get(f"/api/recipe/{recipe_id}/edit").json()
    assert form["changed"] == [] and form["values"]["ingredients"]
    assert client.get(f"/api/recipe/{recipe_id}").json()["edit"]["edited"] is False

    saved = client.put(f"/api/recipe/{recipe_id}/edit",
                       json={"title": "HTTP Linguine", "total_minutes": 44}).json()
    assert saved["changed"] == ["title", "total_minutes"]

    recipe = client.get(f"/api/recipe/{recipe_id}").json()
    assert recipe["title"] == "HTTP Linguine" and recipe["total_minutes"] == 44
    assert recipe["time_is_estimate"] is False
    assert recipe["edit"]["edited"] is True
    assert recipe["edit"]["fields"] == ["title", "total_minutes"]
    assert client.get(f"/api/recipe/{recipe_id}/edit").json()["book"]["title"] != "HTTP Linguine"

    assert client.delete(f"/api/recipe/{recipe_id}/edit").status_code == 200
    assert client.get(f"/api/recipe/{recipe_id}").json()["edit"]["edited"] is False


@pytest.mark.parametrize("body", [
    {"title": ""},                      # refused by the editor's own rules
    {"total_minutes": 0},               # refused by the request model
    {"ingredients": ["x"] * 201},
    {"title": "x" * 201},
])
def test_http_refuses_bad_corrections(client, body):
    recipe_id = _http_linguine(client)
    response = client.put(f"/api/recipe/{recipe_id}/edit", json=body)
    assert response.status_code == 422
    assert client.get(f"/api/recipe/{recipe_id}").json()["edit"]["edited"] is False


def test_http_unknown_recipe_is_404(client):
    assert client.get("/api/recipe/999999/edit").status_code == 404
    assert client.put("/api/recipe/999999/edit", json={"title": "x"}).status_code == 404
    assert client.delete("/api/recipe/999999/edit").status_code == 404


def test_the_recipe_page_loads_the_editor(client):
    html = client.get("/recipe/1").text
    assert "/static/edit.js" in html and 'id="editor"' in html
    assert client.get("/static/edit.js").status_code == 200
