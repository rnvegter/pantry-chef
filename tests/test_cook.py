"""Scaling a recipe's servings, and the timers cook mode offers.

Most of the lines below are real ones from the cookbooks this was developed
against; the awkward cases are the reason the scaler reads brackets at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

import make_fixtures

from pantry_chef.index import ingest
from pantry_chef.parse.scale import nice_number, scale_line, scale_servings, servings_base
from pantry_chef.parse.timing import find_timers

FIXTURES = Path(__file__).parent / "fixtures"


# --- ingredient lines ---------------------------------------------------------

@pytest.mark.parametrize("line,factor,expected", [
    # The plain cases.
    ("2 tablespoons tomato paste", 2, "4 tablespoons tomato paste"),
    ("½ teaspoon ground cumin", 0.5, "¼ teaspoon ground cumin"),
    ("1 1/2 teaspoons salt", 2, "3 teaspoons salt"),
    ("1-1/2 cups sugar", 2, "3 cups sugar"),
    ("2 to 2½ cups chicken broth", 2, "4 to 5 cups chicken broth"),
    ("About 2 cups flour", 1.5, "About 3 cups flour"),
    ("Garnish: ⅓ cup sliced almonds", 3, "Garnish: 1 cup sliced almonds"),
    # Metric is rounded as a cook weighs, and promoted past a kilo.
    ("250 g cake flour", 1.5, "375 g cake flour"),
    ("680 g chicken thighs", 1.5, "1 kg chicken thighs"),
    ("480 ml broth", 2.5, "1.2 l broth"),
    # A bracket after a measured amount restates it, so it scales too.
    ("1½ cups (338 g) cottage cheese", 2, "3 cups (675 g) cottage cheese"),
    ("8 ounces (2 sticks) unsalted butter", 0.5, "4 ounces (1 stick) unsalted butter"),
    ("1 tbsp (15 ml) soy sauce", 3, "3 tbsp (45 ml) soy sauce"),
    ("½ cup (28-g) French-fried onions", 2, "1 cup (55 g) French-fried onions"),
    ("1 tablespoon minced garlic (3 to 4 cloves)", 2,
     "2 tablespoons minced garlic (6 to 8 cloves)"),
    ("455 g parsnips (about 4 parsnips), peeled", 2, "910 g parsnips (about 8 parsnips), peeled"),
    ("625 g / 625g flour", 2, "1.25 kg / 1.25 kg flour"),
    # After a count, the bracket is the size of each thing, which does not
    # change however many you buy — but a later one restating the total does.
    ("1 (14-oz) can tomatoes", 2, "2 (14-oz) cans tomatoes"),
    ("1 (30-oz [850-g]) bag frozen hash browns", 2, "2 (30-oz [850-g]) bags frozen hash browns"),
    ("4 large (9- to 10-inch) tortillas", 2, "8 large (9- to 10-inch) tortillas"),
    ("2 (15-ounce) cans chickpeas, drained (about 3 cups)", 0.5,
     "1 (15-ounce) can chickpeas, drained (about 1½ cups)"),
    ("1 x 400 g tin chopped tomatoes", 2, "2 x 400 g tin chopped tomatoes"),
    # A hyphenated size names a thing even after a measure.
    ("1 cup coconut milk (about half a 13.5-ounce can)", 2,
     "2 cups coconut milk (about half a 13.5-ounce can)"),
    # Counted things agree with their number.
    ("1 large egg", 2, "2 large eggs"),
    ("2 egg yolks", 0.5, "1 egg yolk"),
    ("1 medium sweet potato, diced", 2, "2 medium sweet potatoes, diced"),
    ("1 clove garlic", 3, "3 cloves garlic"),
    ("Two large onions, sliced", 1.5, "3 large onions, sliced"),
    ("Grated zest and juice of 1 orange", 2, "Grated zest and juice of 2 oranges"),
    # Some books put the only amount in brackets.
    ("Edamame (2 handfuls)", 2, "Edamame (4 handfuls)"),
])
def test_scaling_a_line(line, factor, expected):
    scaled, changed = scale_line(line, factor)
    assert scaled == expected
    assert changed


@pytest.mark.parametrize("line", [
    "Kosher salt",
    "Vegetable oil, for frying",
    "8-inch tortillas",                   # a size, not an amount
    "5-spice powder",
    "100% maple syrup",
    "1. Preheat the oven to 190°C.",      # a method step filed as an ingredient
    "Cooked rice (see page 45)",
])
def test_lines_without_an_amount_are_left_alone(line):
    assert scale_line(line, 2) == (line, False)


def test_scaling_by_one_changes_nothing():
    assert scale_line("1½ cups (338 g) cottage cheese", 1) == (
        "1½ cups (338 g) cottage cheese", False)


@pytest.mark.parametrize("value,expected", [
    (0.5, "½"), (1.5, "1½"), (0.333, "⅓"), (2.66, "2⅔"), (1.96, "2"),
    (12.4, "12½"), (22.6, "23"), (0.01, "⅛"),
])
def test_numbers_come_out_the_way_recipes_write_them(value, expected):
    assert nice_number(value) == expected


@pytest.mark.parametrize("text,base,doubled", [
    ("4", 4, "8"),
    ("4 to 6", 4, "8 to 12"),         # scaled from the lower figure
    ("8 scones", 8, "16 scones"),
    ("12 BARS", 12, "24 BARS"),
    ("", None, ""),
    (None, None, ""),
])
def test_servings(text, base, doubled):
    assert servings_base(text) == base
    assert scale_servings(text, 2) == doubled


# --- timers -----------------------------------------------------------------------

def _timed(text):
    return [(t["label"], t["seconds"], t["max_seconds"]) for t in find_timers(text)]


@pytest.mark.parametrize("text,expected", [
    ("Simmer for 20 minutes.", [("20 min", 1200, None)]),
    ("Bake until golden, 8 to 10 minutes.", [("8–10 min", 480, 600)]),
    ("Cook 2½ to 3 minutes more.", [("2½–3 min", 150, 180)]),
    ("Bake 1 hour 15 minutes.", [("1 h 15 min", 4500, None)]),
    ("Roast 1-1/2 hours.", [("1 h 30 min", 5400, None)]),
    ("Rest for half an hour.", [("30 min", 1800, None)]),
    ("Stir for a minute or two.", [("1 min", 60, None)]),
    ("Toast the spices for 30 seconds.", [("30 s", 30, None)]),
    ("Fry for one to two minutes, then 5 min more.", [("1–2 min", 60, 120), ("5 min", 300, None)]),
])
def test_timers_are_found_in_a_step(text, expected):
    assert _timed(text) == expected


@pytest.mark.parametrize("text", [
    "Take a 15-minute walk.",           # describes the walk; not a wait
    "Microwave in 30-second bursts.",
    "Refrigerate for at least 24 hours.",   # a wait, not a timer
    "Season with salt and pepper.",
])
def test_things_that_are_not_timers(text):
    assert find_timers(text) == []


def test_timer_positions_are_counted_as_the_browser_counts():
    """The page slices the step with these offsets in JavaScript, which counts
    UTF-16 units; an emoji before the duration is two of them, not one."""
    text = "Fry 🍳 for 5 minutes."
    (timer,) = find_timers(text)
    units = text.encode("utf-16-le")
    assert units[timer["start"] * 2:timer["end"] * 2].decode("utf-16-le") == "5 minutes"


# --- over HTTP ------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    make_fixtures.build_all(FIXTURES)
    db_file = tmp_path_factory.mktemp("cook") / "cook.db"
    ingest([FIXTURES], db_file, workers=1, force=True)
    import os
    previous = os.environ.get("PANTRY_CHEF_DB")
    os.environ["PANTRY_CHEF_DB"] = str(db_file)
    from pantry_chef.web.app import app
    with TestClient(app) as test_client:
        yield test_client
    if previous is None:
        os.environ.pop("PANTRY_CHEF_DB", None)
    else:
        os.environ["PANTRY_CHEF_DB"] = previous


def _a_recipe(client):
    return client.post("/api/search", json={"title": "linguine"}).json()["results"][0]["id"]


def test_the_recipe_scales_over_http(client):
    recipe_id = _a_recipe(client)
    plain = client.get(f"/api/recipe/{recipe_id}").json()
    doubled = client.get(f"/api/recipe/{recipe_id}?scale=2").json()

    assert plain["scale"] == 1 and doubled["scale"] == 2
    assert doubled["servings_base"] == plain["servings_base"]
    assert [i["line"] for i in doubled["ingredients"]] != [i["line"] for i in plain["ingredients"]]
    # The method is not scaled: its times and pan sizes do not work that way.
    assert doubled["steps"] == plain["steps"]
    assert not any(i["unscaled"] for i in plain["ingredients"])


def test_scale_is_bounded(client):
    recipe_id = _a_recipe(client)
    assert client.get(f"/api/recipe/{recipe_id}?scale=0").status_code == 422
    assert client.get(f"/api/recipe/{recipe_id}?scale=21").status_code == 422
    assert client.get(f"/api/recipe/{recipe_id}?scale=-1").status_code == 422


def test_the_recipe_carries_its_timers(client):
    recipe_id = _a_recipe(client)
    data = client.get(f"/api/recipe/{recipe_id}").json()
    assert len(data["step_timers"]) == len(data["steps"])
    found = [(step[t["start"]:t["end"]], t["seconds"])
             for step, timers in zip(data["steps"], data["step_timers"], strict=True)
             for t in timers]
    # The fixture linguine boils for 9 minutes and simmers for 8.
    assert ("9 minutes", 540) in found and ("8 minutes", 480) in found


def test_the_recipe_page_loads_cook_mode(client):
    html = client.get("/recipe/1").text
    assert "/static/cook.js" in html and "/static/cook.css" in html
    assert 'id="cook"' in html and 'id="cookTray"' in html
    for asset in ("/static/cook.js", "/static/cook.css"):
        assert client.get(asset).status_code == 200
