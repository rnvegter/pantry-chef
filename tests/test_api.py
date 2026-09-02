"""Endpoint tests for the web layer, driven through the real FastAPI app."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

import make_fixtures                       # noqa: E402
from pantry_chef.index import ingest            # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """A test client pointed at a freshly indexed database."""
    make_fixtures.build_all(FIXTURES)
    db_file = tmp_path_factory.mktemp("api") / "api.db"
    ingest([FIXTURES], db_file, workers=1, force=True)

    import os
    os.environ["PANTRY_CHEF_DB"] = str(db_file)
    from pantry_chef.web.app import app
    with TestClient(app) as test_client:
        yield test_client
    os.environ.pop("PANTRY_CHEF_DB", None)


# --- search ----------------------------------------------------------------

def test_stats_endpoint(client):
    data = client.get("/api/stats").json()
    assert data["recipes"] == 15
    assert {"name", "recipes"} <= set(data["meals"][0])
    assert data["diets"] and data["allergens"]


def test_search_endpoint(client):
    data = client.post("/api/search", json={
        "have": ["chicken", "lemon", "garlic", "rosemary"]}).json()
    assert data["results"][0]["title"] == "Lemon and Garlic Roast Chicken"
    assert data["results"][0]["missing"] == []


def test_search_filters_combine(client):
    data = client.post("/api/search", json={
        "diets": ["vegetarian"], "free_from": ["gluten"], "meals": ["dessert"]}).json()
    for result in data["results"]:
        assert "vegetarian" in result["diets"]
        assert "gluten" not in result["allergens"]
        assert "dessert" in result["meals"]


def test_recipe_endpoint_uses_the_pantry(client):
    listing = client.post("/api/search", json={"have": ["dark chocolate"]}).json()
    recipe_id = listing["results"][0]["id"]

    blind = client.get(f"/api/recipe/{recipe_id}").json()
    informed = client.get(f"/api/recipe/{recipe_id}",
                          params={"have": "dark chocolate"}).json()

    def held(payload):
        return {i["canonical"] for i in payload["ingredients"]
                if i["have"] and not i["staple"]}

    assert "dark chocolate" in held(informed)
    assert "dark chocolate" not in held(blind)


def test_suggest_endpoint(client):
    assert client.get("/api/suggest", params={"q": ""}).json() == []
    names = [s["name"] for s in client.get("/api/suggest", params={"q": "chick"}).json()]
    assert "chicken" in names


def test_missing_recipe_is_404(client):
    assert client.get("/api/recipe/999999").status_code == 404


# --- library ---------------------------------------------------------------

def test_library_endpoint(client):
    data = client.get("/api/library").json()
    assert data["stats"]["books"] == 3
    assert data["sources"] == []
    assert data["job"]["status"] in {"idle", "done"}


def test_inspect_reports_contents(client):
    data = client.post("/api/library/inspect", json={"path": str(FIXTURES)}).json()
    assert data["ok"] and data["books"] == 3


def test_inspect_rejects_a_bad_path(client):
    assert not client.post(
        "/api/library/inspect", json={"path": "/nope/nowhere"}).json()["ok"]


def test_add_and_remove_a_source(client):
    added = client.post("/api/library/sources", json={"path": str(FIXTURES)})
    assert added.status_code == 200
    source_id = added.json()["id"]

    assert any(s["id"] == source_id for s in client.get("/api/library").json()["sources"])
    assert client.delete(f"/api/library/sources/{source_id}").status_code == 200
    assert client.get("/api/library").json()["sources"] == []


def test_adding_a_bad_folder_is_rejected(client):
    response = client.post("/api/library/sources", json={"path": "/nope/nowhere"})
    assert response.status_code == 400


def test_removing_an_unknown_source_is_404(client):
    assert client.delete("/api/library/sources/424242").status_code == 404


def test_indexing_with_no_folders_is_rejected(client):
    response = client.post("/api/library/index", json={})
    assert response.status_code == 400
    assert "add a folder" in response.json()["detail"]


def test_index_run_reports_progress_then_finishes(client):
    started = client.post("/api/library/index",
                          json={"paths": [str(FIXTURES)], "force": True})
    assert started.status_code == 200
    assert started.json()["status"] == "running"

    for _ in range(200):
        job = client.get("/api/library/job").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)

    assert job["status"] == "done"
    assert job["indexed"] == 3
    assert job["percent"] == 100.0


def test_retrying_a_missing_file_is_404(client):
    response = client.post("/api/library/retry", json={"path": "/nope/gone.epub"})
    assert response.status_code == 404


def test_browse_lists_directories(client):
    data = client.get("/api/library/browse",
                      params={"path": str(FIXTURES.parent)}).json()
    assert data["ok"]
    assert "fixtures" in [d["name"] for d in data["directories"]]


def test_pages_are_served(client):
    assert "Pantry" in client.get("/").text
    assert "Add a folder" in client.get("/library").text
    assert "Directions" in client.get("/recipe/1").text
    assert client.get("/static/app.css").status_code == 200


# --- the recipe card -------------------------------------------------------

def _first_recipe_id(client, **body):
    return client.post("/api/search", json=body).json()["results"][0]["id"]


def test_recipe_defaults_to_metric(client):
    recipe_id = _first_recipe_id(client, title="linguine")
    data = client.get(f"/api/recipe/{recipe_id}").json()
    lines = " ".join(i["line"] for i in data["ingredients"])
    assert " g " in lines or " ml " in lines or "g " in lines


def test_recipe_can_show_the_books_own_units(client):
    recipe_id = _first_recipe_id(client, title="linguine")
    metric = client.get(f"/api/recipe/{recipe_id}").json()
    original = client.get(f"/api/recipe/{recipe_id}",
                          params={"units": "original"}).json()
    # The originals are preserved either way, so the toggle is lossless.
    assert [i["original"] for i in metric["ingredients"]] == \
           [i["original"] for i in original["ingredients"]]
    assert [i["line"] for i in original["ingredients"]] == \
           [i["original"] for i in original["ingredients"]]


def test_recipe_carries_everything_the_card_needs(client):
    recipe_id = _first_recipe_id(client, title="linguine")
    data = client.get(f"/api/recipe/{recipe_id}").json()
    for key in ("title", "book", "servings", "total_minutes", "difficulty",
                "steps", "ingredients", "meals", "diets", "allergens", "page"):
        assert key in data, key
    assert data["difficulty"] in {"Easy", "Medium", "Hard"}
    assert isinstance(data["steps"], list) and data["steps"]


def test_all_caps_titles_are_recased_for_display(client):
    data = client.post("/api/search", json={"limit": 50}).json()
    for result in data["results"]:
        assert not result["title"].isupper(), result["title"]
        assert "title_original" in result


def test_recipe_photo_is_served(client):
    listing = client.post("/api/search", json={"title": "linguine"}).json()
    recipe = listing["results"][0]
    assert recipe["has_image"]
    assert recipe["image_url"] == f"/api/recipe/{recipe['id']}/image"

    response = client.get(recipe["image_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"
    assert "max-age" in response.headers.get("cache-control", "")


def test_a_recipe_without_a_photo_says_so(client):
    # The loosely-structured fixture book carries no images.
    listing = client.post("/api/search", json={"limit": 100}).json()
    without = [r for r in listing["results"] if not r["has_image"]]
    for recipe in without:
        assert recipe["image_url"] is None
        assert client.get(f"/api/recipe/{recipe['id']}/image").status_code == 404


def test_photo_for_an_unknown_recipe_is_404(client):
    assert client.get("/api/recipe/999999/image").status_code == 404


# --- autocomplete -----------------------------------------------------------

def test_complete_endpoint_per_field(client):
    titles = client.get("/api/complete", params={"field": "title", "q": "lem"}).json()
    assert titles and any("Lemon" in t["value"] for t in titles)
    assert {"value", "recipes"} <= set(titles[0])

    authors = client.get("/api/complete", params={"field": "author", "q": "cook"}).json()
    assert authors and authors[0]["value"] == "A. Cook"

    books = client.get("/api/complete", params={"field": "book", "q": "small"}).json()
    assert books and "Small Kitchen" in books[0]["value"]


def test_complete_waits_for_two_characters(client):
    # One letter would match most of the library and help nobody.
    assert client.get("/api/complete", params={"field": "author", "q": "a"}).json() == []
    assert client.get("/api/complete", params={"field": "title", "q": ""}).json() == []


def test_complete_suggestions_are_real_searches(client):
    """Every suggestion must be a value that actually returns results —
    otherwise picking one from the list would come back empty."""
    for suggestion in client.get(
            "/api/complete", params={"field": "author", "q": "cook"}).json():
        found = client.post("/api/search", json={"author": suggestion["value"]}).json()
        assert found["results"], suggestion["value"]

    for suggestion in client.get(
            "/api/complete", params={"field": "title", "q": "lem"}).json():
        found = client.post("/api/search", json={"title": suggestion["value"]}).json()
        assert found["results"], suggestion["value"]


def test_complete_titles_are_shown_recased(client):
    for suggestion in client.get(
            "/api/complete", params={"field": "title", "q": "lem"}).json():
        assert not suggestion["value"].isupper()


def test_complete_respects_the_limit(client):
    many = client.get("/api/complete",
                      params={"field": "title", "q": "a", "limit": 3}).json()
    assert len(many) <= 3


def test_complete_with_an_unknown_field_falls_back_to_titles(client):
    data = client.get("/api/complete", params={"field": "nonsense", "q": "lem"}).json()
    assert data and any("Lemon" in d["value"] for d in data)


# --- searching by title and author over HTTP --------------------------------

def test_search_by_title_and_author(client):
    by_title = client.post("/api/search", json={"title": "linguine"}).json()
    assert by_title["results"]

    by_author = client.post("/api/search", json={"author": "A. Cook"}).json()
    assert by_author["results"]

    nothing = client.post("/api/search", json={"author": "not a real author"}).json()
    assert nothing["results"] == []


def test_stats_offers_authors_and_books(client):
    data = client.get("/api/stats").json()
    assert data["authors"] and {"name", "recipes"} <= set(data["authors"][0])
    assert data["book_titles"]
    # The book *count* must survive alongside the book *titles*.
    assert isinstance(data["books"], int) and data["books"] == 3
