"""Favourites: saving recipes, and keeping them through a re-read of the library.

The design question these tests pin down is identity. A --force re-index deletes
a book's recipes and inserts them again with new ids, so a favourite cannot be a
recipe id. It is "the Nth recipe called X in book Y" instead.
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

from pantry_chef.db import (
    add_favourite,
    connect,
    favourite_ids,
    favourite_summary,
    insert_recipes,
    remove_favourite,
    upsert_book,
)
from pantry_chef.index import ingest
from pantry_chef.models import Book, Recipe, RecipeIngredient
from pantry_chef.search import Query, get_recipe, search

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    """A database built from the fixture cookbooks, shared read-only."""
    make_fixtures.build_all(FIXTURES)
    path = tmp_path_factory.mktemp("fav") / "library.db"
    ingest([FIXTURES], path, workers=1, force=True)
    return path


@pytest.fixture
def db(library, tmp_path):
    """A private, writable copy, so tests can save favourites freely."""
    target = tmp_path / "favourites.db"
    shutil.copy(library, target)
    return connect(target)


def _ids(conn, limit=2):
    return [row[0] for row in conn.execute(
        "SELECT id FROM recipes ORDER BY id LIMIT ?", (limit,))]


# --- storage ----------------------------------------------------------------

def test_round_trip(db):
    (recipe_id,) = _ids(db, 1)
    assert favourite_ids(db) == set()

    assert add_favourite(db, recipe_id)
    assert favourite_ids(db) == {recipe_id}

    assert remove_favourite(db, recipe_id)
    assert favourite_ids(db) == set()
    assert not remove_favourite(db, recipe_id), "removing twice reports nothing removed"


def test_saving_twice_is_one_favourite(db):
    (recipe_id,) = _ids(db, 1)
    add_favourite(db, recipe_id)
    add_favourite(db, recipe_id)
    assert favourite_summary(db)["total"] == 1


def test_a_missing_recipe_cannot_be_saved(db):
    assert not add_favourite(db, 999_999)


def test_favourites_survive_a_forced_reindex(tmp_path):
    """The reason favourites are not keyed on recipe id."""
    make_fixtures.build_all(FIXTURES)
    database = tmp_path / "reindex.db"
    ingest([FIXTURES], database, workers=1, force=True)

    conn = connect(database)
    before = conn.execute(
        "SELECT id, title FROM recipes WHERE title LIKE '%Linguine%'").fetchone()
    add_favourite(conn, before["id"])
    conn.close()

    ingest([FIXTURES], database, workers=1, force=True)

    conn = connect(database, read_only=True)
    saved = favourite_ids(conn)
    assert len(saved) == 1
    (after_id,) = saved
    assert after_id != before["id"], "the re-index should have issued new ids"
    title = conn.execute(
        "SELECT title FROM recipes WHERE id = ?", (after_id,)).fetchone()[0]
    assert title == before["title"]


def test_a_repeated_title_saves_only_that_one(tmp_path):
    """One real cookbook has seven recipes called "Dough"; saving the second
    must not save all seven."""
    conn = connect(tmp_path / "dupes.db")
    book_id = upsert_book(conn, Book(path="/books/a.epub", sha256="x",
                                     title="A", status="indexed"))
    insert_recipes(conn, book_id, [
        Recipe(title="Dough", order_in_book=i,
               ingredients=[RecipeIngredient(canonical="flour", display="flour")])
        for i in range(3)
    ])
    conn.commit()

    ids = [row[0] for row in conn.execute(
        "SELECT id FROM recipes ORDER BY order_in_book")]
    add_favourite(conn, ids[1])
    assert favourite_ids(conn) == {ids[1]}


def test_a_favourite_whose_book_moved_is_kept_and_reported(db):
    """Deleting it would lose it for good; keeping it means it comes back when
    the book does."""
    (recipe_id,) = _ids(db, 1)
    add_favourite(db, recipe_id)
    db.execute("UPDATE favourites SET book_path = '/moved/elsewhere.epub'")
    db.commit()

    assert favourite_ids(db) == set()
    assert favourite_summary(db) == {"total": 1, "available": 0, "missing": 1}


def test_an_old_database_without_the_table_still_searches(library, tmp_path):
    """Search opens the database read-only, which never runs the schema, so a
    library indexed before favourites existed has no table until something
    writes to it. That used to crash every search."""
    target = tmp_path / "old.db"
    shutil.copy(library, target)
    raw = sqlite3.connect(target)
    raw.execute("DROP TABLE IF EXISTS favourites")
    raw.commit()
    raw.close()

    conn = connect(target, read_only=True)
    results, _info = search(conn, Query())
    assert results and not any(r.is_favourite for r in results)
    assert search(conn, Query(favourites_only=True))[0] == []
    assert favourite_summary(conn) == {"total": 0, "available": 0, "missing": 0}


# --- search -----------------------------------------------------------------

def test_favourites_only_lists_what_was_saved(db):
    first, _second = _ids(db)
    add_favourite(db, first)

    results, _info = search(db, Query(favourites_only=True))
    assert [r.recipe_id for r in results] == [first]


def test_every_result_carries_its_favourite_state(db):
    (first,) = _ids(db, 1)
    add_favourite(db, first)

    results, _info = search(db, Query(limit=200))
    assert {r.recipe_id for r in results if r.is_favourite} == {first}
    assert get_recipe(db, first).is_favourite


def test_filters_narrow_the_favourites_too(db):
    """The Favourites tab keeps the shared filters, so "a saved vegetarian
    dinner" is a question it can answer."""
    for recipe_id in _ids(db, 200):
        add_favourite(db, recipe_id)

    everything, _i = search(db, Query(favourites_only=True, limit=200))
    vegetarian, _j = search(db, Query(favourites_only=True, diets=["vegetarian"],
                                      limit=200))
    assert 0 < len(vegetarian) < len(everything)
    assert all("vegetarian" in r.diets for r in vegetarian)


# --- over HTTP ----------------------------------------------------------------

@pytest.fixture
def client(library, tmp_path, monkeypatch):
    target = tmp_path / "http.db"
    shutil.copy(library, target)
    monkeypatch.setenv("PANTRY_CHEF_DB", str(target))
    from pantry_chef.web.app import app
    with TestClient(app) as test_client:
        yield test_client


def test_http_round_trip(client):
    recipe_id = client.post("/api/search", json={"limit": 1}).json()["results"][0]["id"]

    assert client.get("/api/favourites").json()["total"] == 0

    saved = client.put(f"/api/favourites/{recipe_id}").json()
    assert saved["is_favourite"] and saved["available"] == 1

    card = client.post("/api/search", json={"favourites_only": True}).json()
    assert [r["id"] for r in card["results"]] == [recipe_id]
    assert card["results"][0]["is_favourite"]
    assert client.get(f"/api/recipe/{recipe_id}").json()["is_favourite"]

    removed = client.delete(f"/api/favourites/{recipe_id}").json()
    assert not removed["is_favourite"] and removed["available"] == 0


def test_http_unknown_recipe_is_404(client):
    assert client.put("/api/favourites/999999").status_code == 404
    assert client.delete("/api/favourites/999999").status_code == 404


def test_http_facets_describe_the_favourites(client):
    """With the Favourites tab open, the counts on the filter buttons have to
    count favourites, not the whole library."""
    recipe_id = client.post("/api/search", json={"limit": 1}).json()["results"][0]["id"]
    client.put(f"/api/favourites/{recipe_id}")

    facets = client.post("/api/search", json={"favourites_only": True}).json()["facets"]
    assert sum(facets["meal"].values()) <= 2   # one recipe, at most a couple of meals


def test_the_saved_copy_is_the_one_shown_when_editions_collapse(db):
    """The fixture library holds the same book in three editions, so a dish
    appears three times and search shows one. It must be the copy you saved:
    otherwise the dish looks unsaved, and saving it again quietly creates a
    second favourite for the same thing."""
    title = "Lemon and Garlic Roast Chicken"
    copies = [row[0] for row in db.execute(
        "SELECT id FROM recipes WHERE title = ? ORDER BY id", (title,))]
    assert len(copies) >= 2, "the fixtures should hold this dish in several editions"

    saved = copies[-1]          # deliberately not the copy kept by default
    add_favourite(db, saved)

    results, info = search(db, Query(limit=200))
    shown = [r for r in results if r.title == title]
    assert info["duplicates_collapsed"] > 0
    assert [r.recipe_id for r in shown] == [saved]
    assert shown[0].is_favourite
