"""The shopping list: recipes on it, their ingredients added up by aisle."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

import make_fixtures

from pantry_chef import shopping
from pantry_chef.db import connect
from pantry_chef.index import ingest
from pantry_chef.shopping import _Item, aisle_for

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    make_fixtures.build_all(FIXTURES)
    path = tmp_path_factory.mktemp("shop") / "library.db"
    ingest([FIXTURES], path, workers=1, force=True)
    return path


@pytest.fixture
def db(library, tmp_path):
    target = tmp_path / "shop.db"
    shutil.copy(library, target)
    conn = connect(target)
    yield conn
    conn.close()


def _recipe(conn, like):
    return conn.execute("SELECT id FROM recipes WHERE title LIKE ? ORDER BY id LIMIT 1",
                        (f"%{like}%",)).fetchone()[0]


def _items(listing):
    return {i["key"]: i for aisle in listing["aisles"] for i in aisle["items"]}


def _cupboard(listing):
    return {i["key"]: i for i in listing["cupboard"]}


# --- adding up ----------------------------------------------------------------------

@pytest.mark.parametrize("lines,expected", [
    (["200 g linguine", "150 g linguine"], "350 g"),
    (["1 kg beef", "500 g beef"], "1.5 kg"),
    (["300 ml stock", "0.5 l stock"], "800 ml"),
    (["2 tbsp olive oil", "3 tbsp olive oil"], "5 tbsp"),
    (["1 tsp salt", "1 tsp salt"], "2 tsp"),
    (["2 eggs", "1 egg"], "3"),
    (["1 cup milk"], "240 ml"),
    (["1 lb beef"], "455 g"),
])
def test_amounts_add_up_where_they_honestly_can(lines, expected):
    item = _Item("x")
    for line in lines:
        item.add(line)
    assert item.amount() == expected


def test_amounts_that_cannot_be_added_are_both_shown():
    item = _Item("garlic")
    item.add("4 cloves garlic")
    item.add("1 tbsp minced garlic")
    assert item.amount() == "1 tbsp + 4 cloves"


def test_garlic_counted_before_the_word_is_still_cloves():
    """"3 garlic cloves" puts the number before "garlic"; it is cloves either way."""
    item = _Item("garlic")
    item.add("3 garlic cloves")
    item.add("2 cloves garlic")
    assert item.amount() == "5 cloves"


@pytest.mark.parametrize("canonical,aisle", [
    ("onion", "Fruit & veg"),
    ("broccoli floret", "Fruit & veg"),
    ("chicken thigh", "Meat"),
    ("beef short rib", "Meat"),
    ("salmon", "Fish & seafood"),
    ("egg", "Dairy, eggs & chilled"),
    ("tofu", "Dairy, eggs & chilled"),
    ("linguine", "Dry goods & baking"),
    ("powdered sugar", "Dry goods & baking"),
    ("coconut milk", "Tins, jars & sauces"),
    ("chicken stock", "Tins, jars & sauces"),
    ("coconut oil", "Oils & vinegars"),
    ("garlic powder", "Herbs & spices"),
    ("pea", "Frozen"),
    ("red wine", "Drinks"),
    ("celery rib", "Fruit & veg"),         # not meat, whatever "rib" suggests
])
def test_aisles(canonical, aisle):
    assert aisle_for(canonical) == aisle


# --- the list -----------------------------------------------------------------------

def test_an_empty_list(db):
    listing = shopping.build(db)
    assert listing["recipes"] == [] and listing["aisles"] == []
    assert listing["counts"] == {"items": 0, "ticked": 0}


def test_one_recipe_at_its_servings_and_scaled(db):
    linguine = _recipe(db, "Linguine")
    shopping.add(db, linguine)
    items = _items(shopping.build(db))
    assert items["parmesan"]["amount"] == "50 g"
    assert items["garlic"]["amount"] == "3 cloves"
    assert _cupboard(shopping.build(db))["olive oil"]["amount"] == "2 tbsp"

    shopping.add(db, linguine, 2)          # adding again changes the servings
    listing = shopping.build(db)
    assert len(listing["recipes"]) == 1 and listing["recipes"][0]["scale"] == 2
    items = _items(listing)
    assert items["parmesan"]["amount"] == "100 g"
    assert items["garlic"]["amount"] == "6 cloves"
    assert _cupboard(listing)["olive oil"]["amount"] == "4 tbsp"


def test_two_recipes_share_their_ingredients(db):
    linguine, chicken = _recipe(db, "Linguine"), _recipe(db, "Roast Chicken")
    shopping.add(db, linguine)
    shopping.add(db, chicken)
    listing = shopping.build(db)
    oil = _cupboard(listing)["olive oil"]
    assert oil["amount"] == "5 tbsp"                       # 2 + 3
    assert len(oil["recipes"]) == 2
    assert {"Fruit & veg", "Meat"} <= {a["name"] for a in listing["aisles"]}
    # Aisles come in the order a shop is walked.
    names = [a["name"] for a in listing["aisles"]]
    assert names == [a for a in shopping.AISLES if a in names]


def test_staples_are_listed_apart(db):
    shopping.add(db, _recipe(db, "Linguine"))
    listing = shopping.build(db)
    assert "olive oil" in _cupboard(listing)
    assert "olive oil" not in _items(listing)


def test_ticks_are_kept_and_forgotten_with_their_recipe(db):
    linguine = _recipe(db, "Linguine")
    shopping.add(db, linguine)
    shopping.tick(db, "parmesan", True)
    listing = shopping.build(db)
    assert _items(listing)["parmesan"]["ticked"]
    assert listing["counts"]["ticked"] == 1

    shopping.remove(db, linguine)
    shopping.add(db, linguine)
    assert not _items(shopping.build(db))["parmesan"]["ticked"], \
        "a tick for an ingredient no longer needed should not survive to the next list"


def test_extras(db):
    first = shopping.add_extra(db, "  paper   towels ")
    shopping.add_extra(db, "a bottle of wine")
    listing = shopping.build(db)
    assert [e["text"] for e in listing["extras"]] == ["paper towels", "a bottle of wine"]
    assert listing["counts"]["items"] == 2

    assert shopping.set_extra_done(db, first, True)
    assert shopping.build(db)["counts"]["ticked"] == 1
    assert shopping.remove_extra(db, first)
    assert not shopping.remove_extra(db, first)
    with pytest.raises(ValueError, match="nothing to add"):
        shopping.add_extra(db, "   ")
    with pytest.raises(ValueError, match="under"):
        shopping.add_extra(db, "x" * 201)


def test_clearing_starts_again(db):
    shopping.add(db, _recipe(db, "Linguine"))
    shopping.tick(db, "parmesan", True)
    shopping.add_extra(db, "paper towels")
    shopping.clear(db)
    listing = shopping.build(db)
    assert listing["recipes"] == [] and listing["extras"] == []
    assert db.execute("SELECT COUNT(*) FROM shopping_ticks").fetchone()[0] == 0


def test_titles_are_shown_as_on_the_recipe_page(db):
    linguine = _recipe(db, "Linguine")
    db.execute("UPDATE recipes SET title = 'WEEKNIGHT TOMATO LINGUINE' WHERE id = ?", (linguine,))
    db.commit()
    shopping.add(db, linguine)
    assert shopping.build(db)["recipes"][0]["title"] == "Weeknight Tomato Linguine"


def test_bad_servings_are_refused(db):
    with pytest.raises(ValueError):
        shopping.add(db, _recipe(db, "Linguine"), 0)
    with pytest.raises(ValueError):
        shopping.add(db, _recipe(db, "Linguine"), 21)
    assert shopping.add(db, 999_999) is False


def test_the_list_survives_a_forced_reindex(tmp_path):
    make_fixtures.build_all(FIXTURES)
    database = tmp_path / "reindex.db"
    ingest([FIXTURES], database, workers=1, force=True)
    conn = connect(database)
    before = _recipe(conn, "Linguine")
    shopping.add(conn, before, 1.5)
    conn.close()

    ingest([FIXTURES], database, workers=1, force=True)

    conn = connect(database)
    (entry,) = shopping.entries(conn)
    assert entry["id"] != before and entry["scale"] == 1.5
    assert "Linguine" in entry["title"]
    conn.close()


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


def test_http_round_trip(client):
    recipe_id = _http_linguine(client)
    assert client.get("/api/shopping/summary").json() == {"recipes": 0}
    assert client.get(f"/api/recipe/{recipe_id}").json()["shopping"] is None

    added = client.put(f"/api/shopping/recipes/{recipe_id}", json={"scale": 1.5}).json()
    assert added == {"id": recipe_id, "scale": 1.5, "recipes": 1}
    assert client.get(f"/api/recipe/{recipe_id}").json()["shopping"] == {"scale": 1.5}
    found = client.post("/api/search", json={"title": "linguine"}).json()["results"]
    assert {r["id"]: r["on_list"] for r in found}[recipe_id] is True

    listing = client.get("/api/shopping").json()
    assert listing["recipes"][0]["id"] == recipe_id
    key = listing["aisles"][0]["items"][0]["key"]
    assert client.put(f"/api/shopping/ticks/{key}", json={"ticked": True}).status_code == 200
    assert client.get("/api/shopping").json()["counts"]["ticked"] == 1

    extra = client.post("/api/shopping/extras", json={"text": "paper towels"}).json()["id"]
    assert client.patch(f"/api/shopping/extras/{extra}", json={"done": True}).status_code == 200
    assert client.delete(f"/api/shopping/extras/{extra}").status_code == 200

    assert client.delete(f"/api/shopping/recipes/{recipe_id}").json()["recipes"] == 0
    assert client.delete("/api/shopping").status_code == 200


@pytest.mark.parametrize("scale", [0, 21, -1])
def test_http_refuses_bad_servings(client, scale):
    recipe_id = _http_linguine(client)
    assert client.put(f"/api/shopping/recipes/{recipe_id}",
                      json={"scale": scale}).status_code == 422


def test_http_unknown_things_are_404(client):
    assert client.put("/api/shopping/recipes/999999", json={"scale": 1}).status_code == 404
    assert client.delete("/api/shopping/recipes/999999").status_code == 404
    assert client.patch("/api/shopping/extras/999999", json={"done": True}).status_code == 404
    assert client.delete("/api/shopping/extras/999999").status_code == 404
    assert client.post("/api/shopping/extras", json={"text": "  "}).status_code == 422


def test_the_shopping_page_is_served(client):
    html = client.get("/shopping").text
    assert "/static/shopping.js" in html and "/static/nav.js" in html
    assert "<script>" not in html
    for page in ("/", "/library", "/recipe/1", "/shopping"):
        assert 'href="/shopping"' in client.get(page).text, page
