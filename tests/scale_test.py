"""Generate a library-sized corpus and measure ingest and query performance.

The target is the brief: 500+ cookbooks, scaling upward. Real cookbooks carry
roughly 80-120 recipes, so 500 books is ~50,000 recipes and several hundred
thousand ingredient links -- enough to show whether the schema holds up.
"""

from __future__ import annotations

import random
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from make_fixtures import _xhtml                    # noqa: E402
from pantry_chef.db import connect, stats                # noqa: E402
from pantry_chef.index import ingest                     # noqa: E402
from pantry_chef.search import Query, search             # noqa: E402

PROTEINS = ["chicken thighs", "chicken breast", "pork belly", "beef shin", "lamb shoulder",
            "salmon fillets", "cod loin", "prawns", "tofu", "chickpeas", "lentils",
            "duck legs", "sausages", "mackerel", "halloumi", "tempeh", "squid"]
VEG = ["onions", "leeks", "fennel", "carrots", "celeriac", "courgettes", "aubergine",
       "cherry tomatoes", "spinach", "kale", "broccoli", "cauliflower", "mushrooms",
       "sweet potatoes", "peppers", "cabbage", "peas", "asparagus", "beetroot", "squash"]
AROMATICS = ["garlic", "ginger", "shallots", "spring onions", "red chilli", "lemongrass"]
PANTRY = ["olive oil", "soy sauce", "fish sauce", "coconut milk", "double cream",
          "white wine", "chicken stock", "tomato puree", "dijon mustard", "tahini",
          "rice vinegar", "honey", "maple syrup", "miso paste", "harissa"]
HERBS = ["parsley", "coriander", "thyme", "rosemary", "basil", "dill", "mint", "sage"]
SPICES = ["cumin", "smoked paprika", "turmeric", "cinnamon", "coriander seed",
          "fennel seed", "star anise", "cardamom", "black pepper", "chilli flakes"]
STARCH = ["basmati rice", "linguine", "orzo", "couscous", "new potatoes", "polenta",
          "rice noodles", "flatbreads", "bulgur wheat", "gnocchi"]

UNITS = ["g", "tbsp", "tsp", "ml", "cloves", "sprigs", ""]
STYLES = ["Roast", "Braised", "Charred", "Crispy", "Slow-Cooked", "One-Pan", "Spiced",
          "Grilled", "Pan-Fried", "Sticky", "Smoky", "Herby", "Whipped", "Silky"]
DISHES = ["Traybake", "Curry", "Stew", "Salad", "Soup", "Bowl", "Skillet", "Pilaf",
          "Ragu", "Tagine", "Gratin", "Noodles", "Broth", "Hash"]
STEPS = [
    "Heat the oil in a large pan over a medium heat.",
    "Season generously and cook until deeply browned all over.",
    "Add the aromatics and fry for two minutes until fragrant.",
    "Pour in the liquid, scraping the base of the pan.",
    "Simmer for {n} minutes until thickened and glossy.",
    "Stir through the herbs and check the seasoning.",
    "Serve hot, with the remaining herbs scattered over.",
]


def make_recipe(rng: random.Random) -> dict:
    """One plausible recipe with a realistic ingredient spread."""
    ingredients = (
        rng.sample(PROTEINS, 1)
        + rng.sample(VEG, rng.randint(2, 4))
        + rng.sample(AROMATICS, rng.randint(1, 3))
        + rng.sample(PANTRY, rng.randint(1, 3))
        + rng.sample(HERBS, rng.randint(1, 2))
        + rng.sample(SPICES, rng.randint(1, 3))
        + rng.sample(STARCH, 1)
    )
    lines = []
    for item in ingredients:
        unit = rng.choice(UNITS)
        amount = rng.choice([1, 2, 3, 4, 200, 300, 400, 500]) if unit in ("g", "ml") \
            else rng.randint(1, 4)
        lines.append(f"{amount}{' ' if unit else ' '}{unit} {item}".strip())
    lines.append("Salt and freshly ground black pepper")

    prep, cook = rng.choice([5, 10, 15, 20, 25]), rng.choice([10, 15, 20, 30, 45, 60, 90])
    return {
        "title": f"{rng.choice(STYLES)} {rng.choice(PROTEINS).title()} {rng.choice(DISHES)}",
        "meta": f"Serves {rng.randint(2, 6)} | Prep {prep} minutes | Cook {cook} minutes",
        "ingredients": lines,
        "method": [s.format(n=rng.randint(5, 30)) for s in STEPS],
    }


def write_book(path: Path, n_recipes: int, rng: random.Random) -> None:
    """Write one EPUB containing `n_recipes` generated recipes."""
    import zipfile

    documents = [("front.xhtml", _xhtml(
        "<h1>Introduction</h1><p>Notes from a small test kitchen.</p>", "Introduction"))]
    for i in range(n_recipes):
        recipe = make_recipe(rng)
        items = "".join(f"<li>{x}</li>" for x in recipe["ingredients"])
        steps = "".join(f"<p>{x}</p>" for x in recipe["method"])
        body = (f'<h2>{recipe["title"]}</h2><p class="meta">{recipe["meta"]}</p>'
                f'<h3>Ingredients</h3><ul>{items}</ul><h3>Method</h3>{steps}')
        documents.append((f"r{i}.xhtml", _xhtml(body, recipe["title"])))

    manifest = "".join(
        f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"/>'
        for i, (n, _) in enumerate(documents))
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(documents)))
    opf = ('<?xml version="1.0" encoding="utf-8"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:title>{path.stem.replace("-", " ").title()}</dc:title>'
           '<dc:creator>Test Kitchen</dc:creator>'
           f'<dc:identifier id="id">urn:uuid:{path.stem}</dc:identifier>'
           f'</metadata><manifest>{manifest}</manifest><spine>{spine}</spine></package>')
    container = ('<?xml version="1.0"?><container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        for name, html in documents:
            zf.writestr(f"OEBPS/{name}", html)


def main(n_books: int, per_book: int, workdir: Path) -> None:
    rng = random.Random(20260822)
    library = workdir / "library"
    db_file = workdir / "scale.db"
    shutil.rmtree(library, ignore_errors=True)
    library.mkdir(parents=True, exist_ok=True)
    db_file.unlink(missing_ok=True)

    print(f"generating {n_books} books x ~{per_book} recipes ...")
    started = time.perf_counter()
    for i in range(n_books):
        write_book(library / f"cookbook-{i:04d}.epub", per_book, rng)
    corpus_bytes = sum(f.stat().st_size for f in library.iterdir())
    print(f"  {time.perf_counter() - started:.1f}s, {corpus_bytes / 1e6:.0f} MB on disk\n")

    print("indexing ...")
    report = ingest([library], db_file, workers=8, progress=None)
    rate = report.recipes / report.seconds if report.seconds else 0
    print(f"  {report.indexed} books, {report.recipes} recipes in {report.seconds:.1f}s "
          f"({rate:,.0f} recipes/s, {report.seconds / max(1, report.indexed) * 1000:.0f} ms/book)")
    print(f"  failures: {report.failed}")
    print(f"  database: {db_file.stat().st_size / 1e6:.0f} MB")
    print(f"  {stats(connect(db_file))}\n")

    conn = connect(db_file, read_only=True)
    queries = {
        "pantry of 8, 45 min cap": Query(
            have=["chicken thighs", "onions", "garlic", "cumin", "basmati rice",
                  "coconut milk", "coriander", "ginger"], max_minutes=45),
        "pantry of 3, generous": Query(
            have=["salmon fillets", "spinach", "lemon"], max_missing=3),
        "nothing missing": Query(
            have=["tofu", "broccoli", "soy sauce", "ginger", "garlic",
                  "rice noodles", "spring onions"], max_missing=0),
        "browse under 20 min": Query(max_minutes=20, sort="quickest"),
        "text search": Query(text="charred aubergine"),
        "exclude + pantry": Query(
            have=["beef shin", "carrots", "onions", "thyme"], without=["white wine"]),
    }

    print("query latency (10 runs each, median)")
    for label, query in queries.items():
        timings = []
        for _ in range(10):
            t0 = time.perf_counter()
            results, info = search(conn, query)
            timings.append((time.perf_counter() - t0) * 1000)
        print(f"  {label:26} {statistics.median(timings):7.1f} ms   "
              f"{len(results):3d} results")


if __name__ == "__main__":
    books = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    per = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/tmp/pantry-scale")
    out.mkdir(parents=True, exist_ok=True)
    main(books, per, out)
