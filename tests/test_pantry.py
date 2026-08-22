"""Test suite covering parsing, extraction, segmentation, storage and search."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pantry_chef.db import connect, stats                                  # noqa: E402
from pantry_chef.extract import format_of, is_supported, read_book         # noqa: E402
from pantry_chef.images import downscale, load_image                  # noqa: E402
from pantry_chef.extract.blocks import blocks_from_html                    # noqa: E402
from pantry_chef.index import ingest                                       # noqa: E402
from pantry_chef.db import add_source, list_sources, remove_source, tag_counts  # noqa: E402
from pantry_chef.jobs import (                                             # noqa: E402
    IndexJobManager, diagnose, diagnose_empty, inspect_folder, list_directories,
)
from pantry_chef.models import display_title, split_steps                  # noqa: E402
from pantry_chef.parse import diet                                         # noqa: E402
from pantry_chef.parse.classify import difficulty                          # noqa: E402
from pantry_chef.parse.metric import (                                     # noqa: E402
    book_metric, convert_amount, convert_text, to_metric_line,
)
from pantry_chef.parse.classify import classify, classify_cuisine, classify_meal  # noqa: E402
from pantry_chef.parse.ingredients import (                                # noqa: E402
    canonicalize, ingredient_score, is_title_case, parse_ingredient_line,
)
from pantry_chef.parse.quantities import parse_quantity                    # noqa: E402
from pantry_chef.parse.segment import find_recipes                         # noqa: E402
from pantry_chef.parse.timing import extract_time                          # noqa: E402
from pantry_chef.search import Query, get_recipe, search, suggest_ingredients  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED_TITLES = [
    "Lemon and Garlic Roast Chicken",
    "Weeknight Tomato Linguine",
    "Slow-Braised Beef Shin",
    "Five-Minute Yoghurt Flatbreads",
    "Overnight Chocolate Cold Brew Pots",
]


@pytest.fixture(scope="session", autouse=True)
def fixtures_exist():
    """Generate the sample cookbooks once per test session."""
    sys.path.insert(0, str(Path(__file__).parent))
    import make_fixtures
    make_fixtures.build_all(FIXTURES)


@pytest.fixture(scope="session")
def indexed_db(tmp_path_factory, fixtures_exist):
    """A database built from the fixture cookbooks."""
    path = tmp_path_factory.mktemp("db") / "test.db"
    ingest([FIXTURES], path, workers=1, force=True)
    return path


# --- quantities ------------------------------------------------------------

@pytest.mark.parametrize("line,qty,unit,rest", [
    ("2 tbsp olive oil", 2.0, "tbsp", "olive oil"),
    ("1 1/2 cups flour", 1.5, "cup", "flour"),
    ("1½ cups whole milk", 1.5, "cup", "whole milk"),
    ("½ tsp ground cumin", 0.5, "tsp", "ground cumin"),
    ("2-3 tablespoons parsley", 2.5, "tbsp", "parsley"),
    ("100g dark chocolate", 100.0, "g", "dark chocolate"),
    ("4 cloves garlic, minced", 4.0, "clove", "garlic, minced"),
    ("a pinch of saffron", 1.0, "pinch", "saffron"),
    ("1 x 400g can chopped tomatoes", 400.0, "g", "chopped tomatoes"),
])
def test_parse_quantity(line, qty, unit, rest):
    got_qty, got_unit, got_rest = parse_quantity(line)
    assert got_qty == pytest.approx(qty)
    assert got_unit == unit
    assert got_rest == rest


def test_parse_quantity_ignores_prose():
    qty, unit, _ = parse_quantity("Preheat the oven to 200C.")
    assert qty is None and unit is None


# --- ingredient identity ---------------------------------------------------

@pytest.mark.parametrize("phrase,canonical", [
    ("spring onions, finely sliced", "scallion"),
    ("green onions", "scallion"),
    ("double cream", "heavy cream"),
    ("plain flour", "flour"),
    ("skinless boneless chicken thighs", "chicken thigh"),
    ("extra-virgin olive oil", "olive oil"),
    ("large free-range eggs", "egg"),
    ("aubergine", "eggplant"),
    ("tinned tomatoes", "canned tomato"),
])
def test_canonicalize(phrase, canonical):
    assert canonicalize(phrase) == canonical


def test_unknown_ingredient_is_kept_not_dropped():
    # An ingredient outside the lexicon must still index, under its own name
    # minus the preparation words that are stripped from everything.
    assert canonicalize("pimenton paste") == "pimenton paste"
    assert canonicalize("smoked pimenton paste") == "pimenton paste"


@pytest.mark.parametrize("a,b", [
    ("beef mince", "minced beef"),
    ("pork mince", "minced pork"),
    ("tinned tomatoes", "chopped tomatoes"),
])
def test_word_order_does_not_change_identity(a, b):
    # Otherwise one ingredient splits into two keys and match rates halve.
    assert canonicalize(a) == canonicalize(b)


def test_conjunction_splits_only_when_both_sides_resolve():
    parts = parse_ingredient_line("Salt and freshly ground black pepper")
    assert {p.canonical for p in parts} == {"salt", "black pepper"}
    assert all(p.is_staple for p in parts)


def test_staples_are_flagged():
    assert parse_ingredient_line("2 tbsp olive oil")[0].is_staple
    assert not parse_ingredient_line("200g linguine")[0].is_staple


def test_note_is_split_from_identity():
    parsed = parse_ingredient_line("3 large eggs, beaten")[0]
    assert parsed.canonical == "egg"
    assert "beaten" in parsed.note


# --- distinguishing ingredients from everything else -----------------------

def test_ingredient_lines_score_above_prose():
    assert ingredient_score("2 tbsp olive oil") >= 0.5
    assert ingredient_score("200g dried linguine") >= 0.5
    assert ingredient_score("Heat the oil in a large frying pan.") < 0.5
    assert ingredient_score("For the sauce") == 0.0


def test_recipe_titles_do_not_score_as_ingredients():
    # Titles are built from food words, so this is the critical distinction.
    for title in ("Lemon and Garlic Roast Chicken", "Weeknight Tomato Linguine",
                  "Five-Minute Yoghurt Flatbreads", "Slow-Braised Beef Shin"):
        assert ingredient_score(title) < 0.5, title


def test_index_entries_are_rejected():
    assert ingredient_score("basil, 24") == 0.0


def test_is_title_case():
    assert is_title_case("Lemon and Garlic Roast Chicken")
    assert not is_title_case("Salt and freshly ground black pepper")


# --- timing ----------------------------------------------------------------

@pytest.mark.parametrize("header,instructions,minutes", [
    ("Total time: 45 minutes", "", 45),
    ("Prep: 15 mins  Cook: 30 mins", "", 45),
    ("Ready in 1 hour 10 minutes", "", 70),
    ("Prep 10 min | Cook 25 min | Serves 4", "", 35),
    ("Total 1-1/2 hours", "", 90),
    ("Total: 1½ hours", "", 90),
    ("Cooking time: 2 hours 30 minutes", "", 150),
    ("Serves 4", "Simmer for 20 minutes. Bake for 25 minutes.", 45),
])
def test_extract_time(header, instructions, minutes):
    assert extract_time(header, instructions).total_minutes == minutes


def test_overnight_is_flagged_but_does_not_inflate_the_total():
    timing = extract_time("Serves 4", "Marinate overnight. Grill for 8 minutes.")
    assert timing.has_long_wait
    assert timing.total_minutes == 8


# --- extraction ------------------------------------------------------------

def test_html_blocks_keep_structure():
    blocks = blocks_from_html(
        "<h2>Title</h2><ul><li>2 eggs</li></ul><p>Beat well.</p><style>x{}</style>")
    kinds = [(b.kind, b.text) for b in blocks]
    assert ("heading", "Title") in kinds
    assert ("list_item", "2 eggs") in kinds
    assert not any("x{}" in text for _, text in kinds)


def test_format_dispatch():
    assert format_of("a.epub") == "epub"
    assert format_of("b.kepub.epub") == "kepub"
    assert format_of("c.azw3") == "azw3"
    assert is_supported("d.mobi") and not is_supported("e.txt")


# --- segmentation, across all three book shapes ----------------------------

@pytest.mark.parametrize("filename", [
    "small-kitchen-well.epub",     # semantic headings and lists
    "small-kitchen-loose.epub",    # no lists, no labels
    "small-kitchen.pdf",           # no markup, font size only
])
def test_finds_every_recipe_and_nothing_else(filename, fixtures_exist):
    blocks, _meta = read_book(FIXTURES / filename)
    recipes = find_recipes(blocks)
    assert [r.title for r in recipes] == EXPECTED_TITLES


def test_front_and_back_matter_are_excluded(fixtures_exist):
    recipes = find_recipes(read_book(FIXTURES / "small-kitchen-well.epub")[0])
    titles = " ".join(r.title.lower() for r in recipes)
    for unwanted in ("index", "copyright", "introduction", "acknowledgements"):
        assert unwanted not in titles


def test_ingredients_and_time_are_recovered(fixtures_exist):
    recipes = find_recipes(read_book(FIXTURES / "small-kitchen-well.epub")[0])
    pasta = next(r for r in recipes if "Linguine" in r.title)
    assert pasta.total_minutes == 20
    assert pasta.servings == "2"
    names = {i.canonical for i in pasta.ingredients}
    assert {"linguine", "garlic", "canned tomato", "parmesan", "basil"} <= names


def test_epub_metadata_is_read(fixtures_exist):
    _blocks, meta = read_book(FIXTURES / "small-kitchen-well.epub")
    assert meta["creator"] == "A. Cook"
    assert "Small Kitchen" in meta["title"]


# --- storage and ingest ----------------------------------------------------

def test_ingest_populates_the_database(indexed_db):
    data = stats(connect(indexed_db))
    assert data["books"] == 3
    assert data["books_failed"] == 0
    assert data["recipes"] == 15          # 5 recipes x 3 book shapes
    assert data["links"] > 0


def test_reindex_skips_unchanged_files(indexed_db):
    report = ingest([FIXTURES], indexed_db, workers=1)
    assert report.skipped == 3
    assert report.indexed == 0


def test_a_broken_file_fails_alone(tmp_path):
    (tmp_path / "broken.epub").write_bytes(b"this is not a zip archive")
    report = ingest([tmp_path], tmp_path / "db.sqlite", workers=1)
    assert report.failed == 1
    assert report.errors and "broken.epub" in report.errors[0][0]


# --- search ----------------------------------------------------------------

def test_full_pantry_match_ranks_first(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(
        have=["chicken", "lemon", "garlic", "rosemary"]))
    assert results[0].title == "Lemon and Garlic Roast Chicken"
    assert results[0].missing == []


def test_time_budget_is_respected(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(max_minutes=25))
    assert results
    assert all(r.total_minutes <= 25 for r in results)


def test_missing_ingredients_are_reported(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(
        have=["tomato", "garlic", "parmesan", "basil"], max_minutes=30))
    pasta = results[0]
    assert pasta.title == "Weeknight Tomato Linguine"
    assert {m.canonical for m in pasta.missing} == {"linguine", "chili flake"}


def test_search_widens_to_related_ingredients(indexed_db):
    # A cook typing "tomatoes" should still match a recipe calling for tinned
    # ones, so the pantry match widens across whole-word variants.
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(have=["tomatoes"], max_missing=8))
    assert any(r.title == "Weeknight Tomato Linguine" for r in results)


def test_duplicates_across_books_are_collapsed(indexed_db):
    # The same five recipes appear in all three fixture books.
    conn = connect(indexed_db, read_only=True)
    results, info = search(conn, Query(have=["chicken", "lemon", "garlic"]))
    assert info["duplicates_collapsed"] >= 1
    assert len({r.title for r in results}) == len(results)


def test_exclusions_remove_recipes(indexed_db):
    conn = connect(indexed_db, read_only=True)
    without, _info = search(conn, Query(
        have=["dark chocolate", "coffee"], without=["cream"]))
    assert all("Chocolate" not in r.title for r in without)


def test_unknown_pantry_items_are_reported(indexed_db):
    conn = connect(indexed_db, read_only=True)
    _results, info = search(conn, Query(have=["unobtanium"]))
    assert "unobtanium" in info["unknown"]


def test_auto_relax_avoids_an_empty_screen(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, info = search(conn, Query(have=["beef"], max_missing=0))
    assert results
    assert info["relaxed_to"] is not None


def test_text_search(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(text="braised"))
    assert results and "Braised" in results[0].title


def test_suggestions_come_from_the_library(indexed_db):
    conn = connect(indexed_db, read_only=True)
    names = [s["name"] for s in suggest_ingredients(conn, "chick")]
    assert "chicken" in names


# --- meal classification ---------------------------------------------------

@pytest.mark.parametrize("title,section,ingredients,expected", [
    # The chapter heading is the author's own filing and outranks everything.
    ("Buttermilk Pancakes", "Breakfast", ["flour", "egg", "sugar"], ["breakfast"]),
    ("Roasted Carrot and Dill Slaw", "Sides", ["carrot", "dill"], ["side"]),
    ("Sticky Toffee Pudding", "Desserts", ["flour", "brown sugar"], ["dessert"]),
    # No heading: fall back to the title.
    ("Buttermilk Pancakes", "", ["flour", "egg", "sugar"], ["breakfast"]),
    ("Chicken Noodle Soup", "", ["chicken", "carrot"], ["lunch"]),
    # No signal at all: a savoury main serves both midday and evening.
    ("Something Unnamed", "", ["chicken", "onion", "garlic"], ["lunch", "dinner"]),
    # Sweet ingredients with nothing savoury read as dessert.
    ("Unnamed Sweet Thing", "", ["sugar", "chocolate", "heavy cream"], ["dessert"]),
])
def test_classify_meal(title, section, ingredients, expected):
    assert classify_meal(title, section, ingredients) == expected


def test_dessert_is_not_also_dinner():
    meals = classify_meal("Chocolate Tart", "Puddings", ["chocolate", "sugar"])
    assert meals == ["dessert"]


def test_explicit_breakfast_beats_the_sweet_ingredient_rule():
    # Pancake batter is sweet and flour-based, but it is still breakfast.
    assert classify_meal(
        "Buttermilk Pancakes", "Breakfast",
        ["flour", "sugar", "egg", "baking powder"]) == ["breakfast"]


# --- cuisine classification ------------------------------------------------

@pytest.mark.parametrize("title,ingredients,expected", [
    ("Chicken Tagine with Preserved Lemon",
     ["chicken", "preserved lemon", "harissa", "couscous"], "north african"),
    ("Chicken Tikka Masala",
     ["chicken", "garam masala", "turmeric", "ghee"], "indian"),
    ("Miso Glazed Aubergine",
     ["miso", "mirin", "eggplant", "sesame oil"], "japanese"),
    ("Spaghetti Carbonara",
     ["spaghetti", "pancetta", "parmesan", "egg"], "italian"),
])
def test_classify_cuisine(title, ingredients, expected):
    cuisine, _score = classify_cuisine(title, "", ingredients)
    assert cuisine == expected


def test_cuisine_declines_when_the_evidence_is_thin():
    # Salt, chicken and onion belong to every kitchen on earth.
    cuisine, _score = classify_cuisine("Roast Chicken", "", ["chicken", "onion", "salt"])
    assert cuisine is None


def test_cuisine_declines_when_two_kitchens_tie():
    # Fish sauce and lime are equally Thai and Vietnamese; refuse to guess.
    cuisine, _score = classify_cuisine("Noodle Bowl", "", ["fish sauce", "lime"])
    assert cuisine is None


# --- pepper is two different ingredients -----------------------------------

@pytest.mark.parametrize("phrase,canonical,staple", [
    ("peppers", "bell pepper", False),
    ("2 red peppers, sliced", "bell pepper", False),
    ("1 tsp pepper", "pepper", True),
    ("freshly ground black pepper", "black pepper", True),
    ("red pepper flakes", "chili flake", False),
])
def test_pepper_the_vegetable_is_not_pepper_the_spice(phrase, canonical, staple):
    parsed = parse_ingredient_line(phrase)[0]
    assert (parsed.canonical, parsed.is_staple) == (canonical, staple)


# --- tags in the database and the search -----------------------------------

def test_tags_are_stored(indexed_db):
    conn = connect(indexed_db, read_only=True)
    meals = dict(tag_counts(conn, "meal"))
    assert meals.get("dessert") and meals.get("dinner")
    assert dict(tag_counts(conn, "cuisine")).get("italian")


def test_meal_filter(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(meals=["dessert"]))
    assert results
    assert all("dessert" in r.meals for r in results)
    assert all("Chocolate" in r.title for r in results)


def test_cuisine_filter(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(cuisines=["italian"]))
    assert results
    assert all(r.cuisine == "italian" for r in results)


def test_meal_and_cuisine_combine(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(meals=["dinner"], cuisines=["italian"]))
    assert all("dinner" in r.meals and r.cuisine == "italian" for r in results)


def test_meal_filter_combines_with_a_pantry(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(
        have=["dark chocolate", "coffee"], meals=["dessert"]))
    assert results and results[0].meals == ["dessert"]


def test_recipe_detail_reflects_the_pantry(indexed_db):
    # Opened from a search, the detail view must not mark held items missing.
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(have=["dark chocolate"], meals=["dessert"]))
    recipe_id = results[0].recipe_id

    blind = get_recipe(conn, recipe_id)
    informed = get_recipe(conn, recipe_id, have=["dark chocolate"])
    held = {i.canonical for i in informed.ingredients if i.have and not i.is_staple}
    assert "dark chocolate" in held
    assert "dark chocolate" not in {
        i.canonical for i in blind.ingredients if i.have and not i.is_staple}


# --- allergens and diets ---------------------------------------------------

def test_allergens_are_detected():
    p = diet.profile(["linguine", "parmesan", "canned tomato", "basil"])
    assert p.allergens == {"gluten", "milk"}


@pytest.mark.parametrize("ingredients,allergen", [
    (["fish sauce", "lime"], "fish"),               # hidden: it is anchovy
    (["worcestershire sauce", "beef"], "fish"),     # hidden: it is anchovy
    (["soy sauce", "tofu"], "gluten"),              # hidden: it is wheat
    (["tahini", "chickpea"], "sesame"),
    (["celeriac", "onion"], "celery"),
    (["mussel", "white wine"], "molluscs"),
    (["shrimp", "garlic"], "crustaceans"),
])
def test_hidden_allergen_sources(ingredients, allergen):
    assert allergen in diet.profile(ingredients).allergens


@pytest.mark.parametrize("ingredients,expected", [
    (["red lentil", "onion", "coconut milk"], {"vegetarian", "vegan", "pescatarian", "no red meat"}),
    (["linguine", "parmesan", "basil"], {"vegetarian", "pescatarian", "no red meat"}),
    (["salmon", "lemon", "dill"], {"pescatarian", "no red meat"}),
    (["chicken", "carrot"], {"no red meat"}),
    (["beef", "red wine"], set()),
])
def test_diets_derived(ingredients, expected):
    assert diet.profile(ingredients).diets == expected


def test_gelatin_is_not_vegetarian():
    # The classic trap: a pudding with no visible meat in it.
    assert "vegetarian" not in diet.profile(["heavy cream", "sugar", "gelatin"]).diets


def test_anchovy_is_not_vegetarian_but_is_pescatarian():
    diets = diet.profile(["anchovy", "olive oil", "garlic"]).diets
    assert "vegetarian" not in diets
    assert "pescatarian" in diets


def test_unrecognised_ingredients_are_counted_not_assumed_safe():
    # This is what stops "no nuts" being claimed about an unknown ingredient.
    p = diet.profile(["onion", "garlic", "some obscure regional paste"])
    assert p.unknown == ["some obscure regional paste"]
    assert not p.is_certain


def test_rennet_caveat_is_raised():
    p = diet.profile(["linguine", "parmesan", "basil"])
    assert "vegetarian" in p.diets
    assert any("rennet" in c for c in p.caveats)


# --- diet filtering in search ----------------------------------------------

def test_diet_filter(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(diets=["vegetarian"]))
    assert results
    assert all("vegetarian" in r.diets for r in results)
    assert all("Chicken" not in r.title and "Beef" not in r.title for r in results)


def test_diets_are_anded_not_ored(indexed_db):
    conn = connect(indexed_db, read_only=True)
    both, _i = search(conn, Query(diets=["vegetarian", "vegan"]))
    veg, _j = search(conn, Query(diets=["vegetarian"]))
    assert len(both) <= len(veg)
    assert all({"vegetarian", "vegan"} <= set(r.diets) for r in both)


def test_free_from_excludes_the_allergen(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(free_from=["gluten"]))
    assert results
    assert all("gluten" not in r.allergens for r in results)


def test_free_from_combines_with_diet(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(diets=["vegetarian"], free_from=["gluten"]))
    assert all(
        "vegetarian" in r.diets and "gluten" not in r.allergens for r in results)


def test_strict_mode_drops_uncertain_recipes(indexed_db):
    conn = connect(indexed_db, read_only=True)
    strict, _i = search(conn, Query(free_from=["nuts"], strict_diet=True))
    assert all(r.n_unknown == 0 for r in strict)


# --- failure diagnosis -----------------------------------------------------

@pytest.mark.parametrize("error,expected", [
    ("BadZipFile: File is not a zip file", "DRM"),
    ("install pymupdf to index PDF cookbooks", "PDF support"),
    ("file too large (512 MB)", "400 MB"),
    ("no reader for '.txt'", "not supported"),
    ("PermissionError: [Errno 13] Permission denied", "could not be opened"),
])
def test_diagnosis_explains_the_cause(error, expected):
    assert expected in diagnose(error).cause


def test_unknown_errors_still_get_advice():
    d = diagnose("something nobody anticipated")
    assert d.cause and d.fix and d.retry_may_help if hasattr(d, "retry_may_help") else True


def test_empty_pdf_is_diagnosed_as_a_scan():
    assert "scanned" in diagnose_empty("pdf").cause


# --- folders ---------------------------------------------------------------

def test_inspect_folder_counts_books(fixtures_exist):
    info = inspect_folder(FIXTURES)
    assert info["ok"] and info["books"] == 3
    assert info["by_format"] == {"epub": 2, "pdf": 1}


def test_inspect_folder_rejects_a_bad_path():
    assert not inspect_folder("/definitely/not/here")["ok"]


def test_list_directories_walks_the_tree(tmp_path):
    (tmp_path / "books").mkdir()
    (tmp_path / ".hidden").mkdir()
    data = list_directories(tmp_path)
    names = [d["name"] for d in data["directories"]]
    assert "books" in names and ".hidden" not in names


def test_sources_round_trip(tmp_path):
    conn = connect(tmp_path / "src.db")
    source_id = add_source(conn, "/tmp/books")
    assert [r["path"] for r in list_sources(conn)] == ["/tmp/books"]
    # Adding the same folder twice must not duplicate it.
    assert add_source(conn, "/tmp/books") == source_id
    assert len(list_sources(conn)) == 1
    assert remove_source(conn, source_id) and not list_sources(conn)


# --- the indexing job ------------------------------------------------------

def test_job_runs_and_reports_progress(tmp_path, fixtures_exist):
    manager = IndexJobManager()
    manager.start([str(FIXTURES)], tmp_path / "job.db", force=True, workers=1)

    for _ in range(200):
        if not manager.running:
            break
        time.sleep(0.05)

    state = manager.snapshot()
    assert state["status"] == "done"
    assert state["indexed"] == 3
    assert state["recipes"] == 15
    assert state["percent"] == 100.0
    assert state["log"]


def test_only_one_job_at_a_time(tmp_path, fixtures_exist):
    manager = IndexJobManager()
    manager.start([str(FIXTURES)], tmp_path / "a.db", force=True, workers=1)
    try:
        with pytest.raises(RuntimeError):
            manager.start([str(FIXTURES)], tmp_path / "b.db")
    finally:
        for _ in range(200):
            if not manager.running:
                break
            time.sleep(0.05)


def test_a_failed_book_is_recorded_with_a_diagnosis(tmp_path):
    (tmp_path / "drm.epub").write_bytes(b"not a zip at all")
    manager = IndexJobManager()
    manager.start([str(tmp_path)], tmp_path / "db.sqlite", force=True, workers=1)
    for _ in range(200):
        if not manager.running:
            break
        time.sleep(0.05)

    conn = connect(tmp_path / "db.sqlite", read_only=True)
    from pantry_chef.db import failed_books
    rows = failed_books(conn)
    assert len(rows) == 1
    assert "DRM" in diagnose(rows[0]["error"]).cause


# --- re-indexing over existing data ----------------------------------------

def test_force_reindex_replaces_rather_than_duplicates(tmp_path, fixtures_exist):
    """Regression: a contentless FTS5 table rejects DELETE, so re-reading an
    already-indexed book used to fail outright once it had recipes."""
    db_file = tmp_path / "reindex.db"
    ingest([FIXTURES], db_file, workers=1, force=True)
    first = stats(connect(db_file))

    report = ingest([FIXTURES], db_file, workers=1, force=True)
    assert report.failed == 0
    assert not report.errors

    second = stats(connect(db_file))
    assert second["recipes"] == first["recipes"]     # replaced, not appended
    assert second["books"] == first["books"]

    # The search index must still be usable and free of stale rows.
    conn = connect(db_file, read_only=True)
    results, _info = search(conn, Query(text="braised"))
    assert len(results) == 1


def test_an_old_fts_table_is_rebuilt(tmp_path, fixtures_exist):
    """A database built before contentless_delete existed is migrated in place."""
    import sqlite3

    db_file = tmp_path / "legacy.db"
    ingest([FIXTURES], db_file, workers=1, force=True)

    # Recreate the search index the old way, without contentless_delete.
    raw = sqlite3.connect(db_file)
    raw.executescript(
        """
        DROP TABLE recipes_fts;
        CREATE VIRTUAL TABLE recipes_fts USING fts5(
            title, ingredients_text, instructions, content='',
            tokenize='unicode61 remove_diacritics 2');
        """
    )
    raw.commit()
    assert "contentless_delete" not in raw.execute(
        "SELECT sql FROM sqlite_master WHERE name='recipes_fts'").fetchone()[0]
    raw.close()

    conn = connect(db_file)      # opening runs the migration
    definition = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='recipes_fts'").fetchone()[0]
    assert "contentless_delete" in definition
    # Rebuilt from the recipes already stored, so text search still works.
    results, _info = search(conn, Query(text="braised"))
    assert results


# --- metric conversion -----------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("2 cups all-purpose flour", "250 g all-purpose flour"),
    ("1/2 cup sugar", "100 g sugar"),
    ("1 cup whole milk", "240 ml whole milk"),          # a liquid stays volume
    ("1 lb ground beef", "455 g ground beef"),
    ("8 oz cream cheese", "225 g cream cheese"),
    ("2 sticks butter", "225 g butter"),
    ("1 pint heavy cream", "470 ml heavy cream"),
])
def test_imperial_becomes_metric(line, expected):
    assert to_metric_line(line) == expected


@pytest.mark.parametrize("line", [
    "200g dark chocolate",          # already metric
    "3 cloves garlic",              # a count, not a measure
    "1 tsp smoked paprika",         # spoons are universal
    "2 tbsp olive oil",
    "Salt and freshly ground black pepper",
])
def test_metric_and_counts_are_left_alone(line):
    assert to_metric_line(line) == line


def test_volume_to_mass_depends_on_the_ingredient():
    # A cup of flour and a cup of honey weigh very different amounts, and a
    # cup of stock should not be given a weight at all.
    assert convert_amount(1, "cup", "flour") == "125 g"
    assert convert_amount(1, "cup", "honey") == "340 g"
    assert convert_amount(1, "cup", "chicken stock") == "240 ml"
    # An ingredient we do not know falls back to volume, which is always true.
    assert convert_amount(1, "cup", "obscure regional paste") == "240 ml"


@pytest.mark.parametrize("line,expected", [
    ("1 lb (454 g) ground beef", "454 g"),
    ("1 cup (240 ml) ketchup", "240 ml"),
    ("1 (26-oz [737-g]) bag frozen fries", "737 g"),
    ("12 to 16 oz (340 to 454 g) chicken", "454 g"),      # upper bound
    ("1 (4- to 5-lb [1.8- to 2.3-kg]) pork butt", "2.3 kg"),
])
def test_the_books_own_metric_is_preferred(line, expected):
    assert book_metric(line) == expected
    assert to_metric_line(line).startswith(expected)


def test_no_book_metric_when_there_is_none():
    assert book_metric("2 cups flour") is None
    assert book_metric("1 (14-oz) can beans") is None     # imperial only


@pytest.mark.parametrize("line,expected", [
    ("2 cups all-purpose flour, plus more for dusting",
     "250 g all-purpose flour, plus more for dusting"),
    ("2 cups rolled oats, for topping", "180 g rolled oats, for topping"),
    ("1 cup heavy cream, plus more for brushing",
     "240 ml heavy cream, plus more for brushing"),
])
def test_trailing_notes_do_not_hide_the_ingredient(line, expected):
    """Regression: "plus more for dusting" used to defeat the density lookup,
    so flour was measured in millilitres instead of grams."""
    assert to_metric_line(line) == expected


def test_bracket_removal_does_not_leave_gaps():
    assert to_metric_line("1 lb (454 g) beef, cut into cubes") == "454 g beef, cut into cubes"


@pytest.mark.parametrize("text,expected", [
    ("Preheat the oven to 350°F.", "Preheat the oven to 175°C."),
    ("Bake at 425 degrees F for 20 minutes.", "Bake at 220°C for 20 minutes."),
    ("Use a 9-inch tin.", "Use a 23 cm tin."),
    ("Roll to 1/4 inch thick.", "Roll to 0.5 cm thick."),
    ("Add 2 lbs of potatoes.", "Add 905 g of potatoes."),
])
def test_prose_conversion(text, expected):
    assert convert_text(text) == expected


def test_the_books_own_celsius_is_preferred():
    # Converting alongside the book's figure would print two different numbers.
    assert convert_text("air fry at 400°F (200°C)") == "air fry at 200°C"


def test_conversion_leaves_metric_prose_untouched():
    assert convert_text("Simmer for 20 minutes.") == "Simmer for 20 minutes."
    assert convert_text("Preheat to 200°C.") == "Preheat to 200°C."


# --- presentation ----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("FASTER THAN TAKEOUT ORANGE CHICKEN", "Faster Than Takeout Orange Chicken"),
    ("SLOPPY JOE FRIES", "Sloppy Joe Fries"),
    ("BBQ CHICKEN AND RANCH PIZZA", "BBQ Chicken and Ranch Pizza"),
    ("MOM'S APPLE PIE", "Mom's Apple Pie"),
    # Entirely lower case is the other styling extreme, and also recased.
    ("fattoush", "Fattoush"),
    ("pasta e fagioli", "Pasta e Fagioli"),
    # Already mixed case: left exactly as the book wrote it.
    ("Slow-Braised Beef Shin", "Slow-Braised Beef Shin"),
    ("Weeknight Tomato Linguine", "Weeknight Tomato Linguine"),
])
def test_display_title(raw, expected):
    assert display_title(raw) == expected


def test_split_steps_strips_existing_numbering():
    steps = split_steps("1. Preheat the oven.\n2) Mix the flour.\nStep 3: Bake.")
    assert steps == ["Preheat the oven.", "Mix the flour.", "Bake."]


def test_split_steps_strips_numbering_written_without_punctuation():
    """Some books set steps as "1 Preheat the oven", with no delimiter."""
    steps = split_steps("1 Preheat the oven.\n2 Mix the flour.\n3 Bake it.")
    assert steps == ["Preheat the oven.", "Mix the flour.", "Bake it."]


def test_a_step_that_really_starts_with_a_number_is_left_alone():
    steps = split_steps("200 g of the mixture goes in the tin.\nBake until golden.")
    assert steps[0].startswith("200 g")


def test_split_steps_breaks_up_a_prose_blob():
    blob = ("Heat the oil in a pan. Add the onion and cook until soft. "
            "Stir in the garlic. Pour in the stock and simmer. Season and serve.")
    steps = split_steps(blob)
    assert len(steps) > 1
    assert all(s.strip() for s in steps)


def test_split_steps_on_nothing():
    assert split_steps("") == []


@pytest.mark.parametrize("ingredients,steps,minutes,text,expected", [
    (6, 4, 20, "Mix and bake.", "Easy"),
    (12, 8, 75, "Braise gently. Reduce the sauce.", "Medium"),
    (18, 14, 200, "Temper the chocolate. Emulsify. Proof overnight.", "Hard"),
])
def test_difficulty(ingredients, steps, minutes, text, expected):
    assert difficulty(ingredients, steps, minutes, text) == expected


# --- searching by title, author and book -----------------------------------

def test_search_by_title(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(title="linguine"))
    assert results
    assert all("linguine" in r.title.lower() for r in results)


def test_title_search_does_not_match_the_ingredient_list(indexed_db):
    # "chicken" appears in the roast chicken's title and nowhere else's, even
    # though other recipes could list it.
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(title="chicken"))
    assert all("chicken" in r.title.lower() for r in results)


def test_search_by_author(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(author="A. Cook"))
    assert results
    hits, _i = search(conn, Query(author="nobody at all"))
    assert hits == []


def test_author_and_book_are_separate_fields(indexed_db):
    conn = connect(indexed_db, read_only=True)
    # The book's title is not its author, and vice versa.
    by_book, _i = search(conn, Query(book="Small Kitchen"))
    assert by_book and all("Small Kitchen" in r.book_title for r in by_book)
    assert search(conn, Query(author="Small Kitchen"))[0] == []

    by_author, _j = search(conn, Query(author="A. Cook"))
    assert by_author
    assert search(conn, Query(book="A. Cook"))[0] == []


def test_author_and_book_combine(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(author="A. Cook", book="Small Kitchen"))
    assert results


def test_search_by_book(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(book="Small Kitchen"))
    assert results


def test_title_search_combines_with_filters(indexed_db):
    conn = connect(indexed_db, read_only=True)
    results, _info = search(conn, Query(title="chocolate", diets=["vegetarian"]))
    assert all("vegetarian" in r.diets for r in results)


def test_odd_punctuation_does_not_break_the_query(indexed_db):
    # FTS5 syntax errors are a real risk with raw user text.
    conn = connect(indexed_db, read_only=True)
    for text in ['"', "NEAR(", "a AND OR b", "*", "'"]:
        search(conn, Query(title=text))
        search(conn, Query(text=text))


# --- spawn safety ----------------------------------------------------------

def test_pool_is_refused_when_main_cannot_be_reimported(monkeypatch):
    """A caller that spawn cannot re-import must fall back to serial parsing
    without ever starting a worker, since spawning re-runs its top level."""
    import sys as _sys
    from pantry_chef import index as index_module

    class FakeMain:
        __file__ = "<stdin>"
        __spec__ = None

    monkeypatch.setitem(_sys.modules, "__main__", FakeMain())
    assert not index_module._main_is_importable()
    assert not index_module.pool_usable(4)


# --- recipe photographs ----------------------------------------------------

def test_each_recipe_is_paired_with_its_photo(fixtures_exist):
    recipes = find_recipes(read_book(FIXTURES / "small-kitchen-well.epub")[0])
    refs = [r.image_ref for r in recipes]
    assert all(refs), "every fixture recipe has a photo beside it"
    assert len(set(refs)) == len(refs), "and each gets a different one"


def test_a_book_without_photos_yields_none(fixtures_exist):
    recipes = find_recipes(read_book(FIXTURES / "small-kitchen-loose.epub")[0])
    assert all(r.image_ref == "" for r in recipes)


def test_covers_and_ornaments_are_not_treated_as_photos(tmp_path):
    """Only photographs should be offered: not covers, logos or rules."""
    import zipfile

    from pantry_chef.extract.epub import MIN_IMAGE_BYTES

    book = tmp_path / "b.epub"
    big = b"\xff\xd8" + b"x" * (MIN_IMAGE_BYTES + 10)
    html = ('<html><body>'
            '<img src="images/cover.jpg"/>'
            '<img src="images/ornament_rule.jpg"/>'
            '<img src="images/tiny.jpg"/>'
            '<img src="images/dinner.jpg"/>'
            '<h2>A Dish</h2></body></html>')
    with zipfile.ZipFile(book, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                    '<rootfile full-path="content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>')
        zf.writestr("content.opf",
                    '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
                    'unique-identifier="i"><metadata '
                    'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    '<dc:identifier id="i">x</dc:identifier></metadata><manifest>'
                    '<item id="d" href="d.xhtml" media-type="application/xhtml+xml"/>'
                    '</manifest><spine><itemref idref="d"/></spine></package>')
        zf.writestr("d.xhtml", html)
        for name in ("cover.jpg", "ornament_rule.jpg", "dinner.jpg"):
            zf.writestr(f"images/{name}", big)
        zf.writestr("images/tiny.jpg", b"\xff\xd8tiny")

    blocks, _meta = read_book(book)
    images = [b.text for b in blocks if b.kind == "image"]
    assert images == ["images/dinner.jpg"]


def test_loading_a_photo_out_of_the_book(fixtures_exist, tmp_path):
    book = FIXTURES / "small-kitchen-well.epub"
    recipes = find_recipes(read_book(book)[0])
    ref = recipes[0].image_ref

    loaded = load_image(str(book), ref, cache_root=tmp_path)
    assert loaded is not None
    data, content_type = loaded
    assert content_type == "image/jpeg"
    assert data[:2] == b"\xff\xd8"          # a real JPEG

    # The second read comes from the cache and matches byte for byte.
    again = load_image(str(book), ref, cache_root=tmp_path)
    assert again is not None and again[0] == data


def test_missing_photos_fail_quietly(fixtures_exist, tmp_path):
    book = str(FIXTURES / "small-kitchen-well.epub")
    assert load_image(book, "images/not-there.jpg", cache_root=tmp_path) is None
    assert load_image("/no/such/book.epub", "x.jpg", cache_root=tmp_path) is None
    assert load_image(book, "", cache_root=tmp_path) is None


def test_oversized_photos_are_downscaled(fixtures_exist):
    """Print artwork runs to megabytes; a page should not have to carry that."""
    import pymupdf

    big = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 3000, 2000))
    for i in range(0, 3000, 5):
        big.set_rect(pymupdf.IRect(i, 0, i + 3, 2000), (i % 255, (i * 3) % 255, 90))
    original = big.tobytes("jpeg", jpg_quality=95)

    smaller = downscale(original)
    assert smaller is not None
    data, content_type = smaller
    assert content_type == "image/jpeg"
    assert len(data) < len(original)
    assert pymupdf.Pixmap(data).width <= 1400


def test_small_photos_are_served_untouched():
    import pymupdf

    small = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 800, 600))
    assert downscale(small.tobytes("jpeg")) is None


def test_photo_reference_survives_the_database(indexed_db):
    conn = connect(indexed_db, read_only=True)
    row = conn.execute(
        "SELECT COUNT(*) FROM recipes WHERE image_ref <> ''").fetchone()
    assert row[0] >= 5
    assert stats(conn)["with_photo"] >= 5
