<img src="pantry_chef/web/static/logo.svg" alt="Pantry Chef" height="44">

Turn a shelf of digital cookbooks into a searchable database, then ask it the
only question that matters on a weeknight: **what can I cook with what I have,
in the time I've got?**

Point it at a folder of EPUB, PDF or Kindle files. It reads every book, finds
the recipes inside, works out each one's ingredients, cooking time, meal type,
cuisine, allergens and diets, converts everything to metric, and builds a local
SQLite index you can search from a browser or the terminal.

Everything runs on your machine. No API keys, no uploads, no network calls.

---

## Quick start

Full setup, troubleshooting and uninstall instructions are in
**[INSTALL.md](INSTALL.md)**; to run it on a server, see
**[DEPLOY.md](DEPLOY.md)**. The short version:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[all,dev]"
```

Index your library (this is the slow part — once):

```bash
.venv/bin/python -m pantry_chef index ~/Books/Cookbooks
```

Search from the browser:

```bash
.venv/bin/python -m pantry_chef serve
```

…or from the terminal:

```bash
.venv/bin/python -m pantry_chef search --have "chicken thighs, garlic, lemon, rice" --time 40
```

…narrowed to a meal, a style of cooking, or a diet:

```bash
.venv/bin/python -m pantry_chef search --have "chicken, coconut milk, ginger" --meal dinner --cuisine thai
```

```bash
.venv/bin/python -m pantry_chef search --diet vegetarian --free-from "gluten, nuts" --time 30
```

…or by name, when you know what you're after:

```bash
.venv/bin/python -m pantry_chef search --title "orange chicken"
.venv/bin/python -m pantry_chef search --author Ottolenghi --meal dinner
```

```
Lemon and Garlic Roast Chicken  [1]
  1h35  ready  4/4 on hand  ·  serves 4
  The Small Kitchen, p.3
```

---

## Commands

| Command | What it does |
|---|---|
| `pantry-chef index <paths…>` | Read books into the database. Re-run any time; unchanged files are skipped. |
| `pantry-chef search --have … --time …` | Rank recipes against your pantry and time budget. |
| `pantry-chef show <id>` | Print one recipe in full. |
| `pantry-chef serve` | Local web app on `http://127.0.0.1:8077`. Search is at `/`, the library manager at `/library`. |
| `pantry-chef stats` | Size and quality of the index. |
| `pantry-chef books` | Every book indexed, and anything that failed. |
| `pantry-chef ingredients <prefix>` | Which ingredient names your library actually knows. |

Useful search flags: `--title "lemon tart"`, `--author` (matches the author
`--author`, `--book`, `--meal breakfast,lunch`, `--cuisine italian,french`,
`--diet vegetarian`, `--free-from "gluten,nuts"`, `--strict`, `--max-missing N`
(how much you'll shop for), `--without "mushrooms"`, `--sort quickest`,
`--timed-only` (skip recipes whose time was estimated rather than stated),
`--no-wait` (skip anything needing an overnight rest).

`pantry-chef stats` lists which meals and cuisines your own library actually
contains, and the web UI only offers the ones it holds.

The database defaults to `data/pantry-chef.db`; override with `--db` or `$PANTRY_CHEF_DB`.

---

## The recipe card

Every result links to its own page at `/recipe/<id>`, opened in a new tab so
your search survives. It is laid out as a recipe card — category, title,
servings, time, difficulty and ingredient count across the top, ingredients
down the left, numbered method on the right — and it prints cleanly.

Two things on it are worth explaining:

- **Difficulty is derived**, not read from the book: it weighs how much there
  is to buy, how many steps there are, how long it runs, and whether the method
  calls for a technique that takes practice (tempering, emulsifying, proving).
- **Steps are reconstructed.** Books are inconsistent — some give a paragraph
  per step, some run the whole method together as prose. Line breaks are
  trusted where they exist, a single blob is split on sentences instead, and
  any numbering the book already applied is stripped so it is not doubled up.

A **Show original units** button flips back to exactly what the book wrote; the
originals are always kept, so the toggle is lossless.

### The photograph

If the book has a photo of the dish, the card shows it. Images are **referenced,
not copied**: a 500-book library holds tens of thousands of photographs, and
duplicating them would cost gigabytes to store artwork already on disk. The
first request extracts the image from the book, downscales it — print artwork
runs to several megabytes a page, which is absurd for a screen — and caches the
result under `data/image-cache/`. Later requests are a file read.

Not every image in a book is a photograph, so covers, logos, ornaments and
rules are filtered out by name and by size. A book with no recipe photos simply
shows none, and the layout is built to look finished without one.

On the six cookbooks this was developed against, 502 of 586 recipes came out
with a photo; the one book contributing none turned out to have no recipe
photography at all.

---

## Metric conversion

Recipes are shown in metric by default. The interesting part is that
volume-to-mass depends on what is being measured: a cup is 240 ml of anything,
but nobody weighs out 240 ml of flour — they want 125 g.

So the conversion is **ingredient-aware**. Known dry goods convert to grams via
a density table, liquids convert to millilitres, and anything unrecognised
falls back to millilitres, which is literally correct even when it is not what
a baker would write.

| The book says | You get | Why |
|---|---|---|
| 2 cups all-purpose flour | 250 g | dry good, known density |
| 1 cup honey | 340 g | dry-ish and much denser than water |
| 1 cup whole milk | 240 ml | a liquid stays a liquid |
| 1 lb ground beef | 455 g | a weight is just a weight |
| 1 tsp smoked paprika | *unchanged* | spoons are already universal |
| 200 g dark chocolate | *unchanged* | already metric |

Two rules keep it honest:

- **The book's own figure always wins.** Modern cookbooks often print both —
  `1 lb (454 g) ground beef`, `400°F (200°C)`. Rather than computing a second,
  slightly different number, the printed metric is used and the imperial
  dropped.
- **Prose is converted too**, so oven temperatures and tin sizes in the method
  come out as `175°C` and `23 cm`.

---

## The library page

`http://127.0.0.1:8077/library` is where the collection is managed, so you
never have to touch the CLI if you'd rather not.

- **Add folders** by typing a path or walking to it with the built-in browser.
  Before you commit, it tells you what it found — "Found 412 books — 380 epub,
  32 pdf".
- **Index** everything, one folder, or one book, and watch it happen: a
  progress bar, the book currently being read, running counts, elapsed time and
  an estimate of what's left, plus a live log. **Stop** halts after the book in
  flight; everything already read is kept.
- **Needs attention** lists whatever didn't work, and this is the part worth
  having. Each entry says what went wrong in plain language and what to do
  about it, with a **Retry** button:

  | What happened | What it usually means | What it tells you to do |
  |---|---|---|
  | `BadZipFile` on an EPUB | DRM, or a truncated download | Remove DRM from your own copy (Calibre + DeDRM), or re-download |
  | Read fine, no recipes found | A scanned PDF — pages are images, so there is no text | Run OCR (Preview, Acrobat, `ocrmypdf`), then re-index |
  | `install pymupdf` | An optional dependency is missing | `pip install pymupdf` |
  | File over 400 MB | Almost always a scan stored as images | OCR it, or leave it out |
  | Unsupported extension | Not a format we read | Convert it with Calibre |

  Books that read cleanly but produced nothing are shown too. They aren't
  errors, but they're the ones you most want to know about — a scanned
  cookbook fails silently otherwise.

Indexing runs in the background, so you can keep searching while it works.

---

## How the extraction works

Cookbooks have no common structure, so recognising a recipe by its title is
hopeless — titles are unconstrained prose. Instead the parser anchors on the
one thing every recipe has and nothing else does: **a run of consecutive
ingredient lines**.

1. **Read** the file into ordered text blocks. EPUB and Kindle books keep their
   headings and lists; PDFs have no markup at all, so headings are recovered by
   measuring each line's font size against the book's modal body size.
2. **Score every line** on how much it reads like an ingredient — a leading
   quantity, a unit, a known food noun, against penalties for starting with a
   cooking verb or running to sentence length.
3. **Find the runs.** Three or more ingredient-ish lines in a row is a recipe.
4. **Work outwards.** The title is the best-fitting line above the run, the
   metadata sits between the two, the method is everything after it.
5. **Parse the parts** — quantities and units, ingredient identity, cooking time.

One problem is worth calling out because it drives much of the design: recipe
titles are *made of food words*. "Lemon and Garlic Roast Chicken" scores as an
ingredient line on every naive test. Three signals separate a name from a list
entry — it's a heading, it's in Title Case, and it carries no digits.

### Ingredient identity

Matching only works if the same thing always gets the same name. `2 tbsp
extra-virgin olive oil`, `Olive oil, for drizzling` and `olive oil` all reduce
to `olive oil`, via unit stripping, descriptor removal, singularisation and a
synonym table that handles regional variants — `spring onion` = `green onion` =
`scallion`, `double cream` = `heavy cream`, `aubergine` = `eggplant`.

Ingredients outside the lexicon are never dropped. They index under their own
cleaned name, so an obscure item still matches when you type the same words.

**Staples** — salt, pepper, oil, butter, flour, water — are assumed present and
never counted as missing. Otherwise every recipe would look equally out of
reach.

### Meal type and cuisine

Both are inferred at index time from three sources, used in strict priority
order rather than pooled:

1. **the chapter heading** — "Puddings", "Breakfast", "Sides". This is the
   author's own filing and beats everything else.
2. **the recipe title** — `pancakes`, `tagine`, `carbonara`, `bibimbap`.
3. **the ingredient signature** — miso + mirin + dashi reads Japanese; harissa
   + preserved lemon + couscous reads North African.

Meal type is deliberately **multi-valued**: most savoury mains are honestly
both lunch and dinner, and forcing one label would make the filter useless.
Sweet wins outright, though — a dessert is not also a dinner. One case worth
knowing: pancakes are sweet, flour-based and full of sugar, but a `Breakfast`
chapter heading still wins over the ingredient signature.

Cuisine is scored, and **declines to answer** below a threshold or when two
kitchens tie — fish sauce and lime are equally Thai and Vietnamese, so an
unlabelled recipe is better than a wrong one. On the 500-book benchmark below,
68% of recipes got a cuisine; the rest are simply "no style", which is the
honest answer for roast chicken.

### Diets and allergens

> **Read this before relying on it.** Detection is **positive-only**: it finds
> the allergens it recognises in an ingredient list. It cannot prove absence.
> An OCR slip, an ingredient the lexicon has never seen, or an allergen that
> only appears in the method text will all be missed. **Do not use this as a
> safety check for a serious allergy** — use it to narrow a long list, then
> read the recipe.

Every recipe is checked against the EU's declarable allergens — gluten,
crustaceans, egg, fish, peanuts, soy, milk, nuts, celery, mustard, sesame,
sulphites, molluscs — and tagged with the diets it fits: vegetarian, vegan,
pescatarian, no red meat.

The mappings deliberately cover the traps a naive ingredient scan misses:

| Looks fine | Actually contains |
|---|---|
| fish sauce, Worcestershire sauce | fish (anchovy) |
| soy sauce, hoisin, miso | gluten (wheat) as well as soy |
| gelatin, suet, lard | meat — so no, that panna cotta isn't vegetarian |
| parmesan, gruyère, manchego | flagged: traditionally made with animal rennet |

Where a judgement is genuinely contested it's raised as a **caveat** on the
recipe rather than acted on silently — vegetarian parmesan is easy to buy, so
the recipe stays tagged vegetarian and tells you why to check.

**Ingredients we don't recognise are counted, not assumed safe.** That count
rides along with each recipe, the interface says "3 ingredients not recognised
— allergen check is incomplete", and `--strict` (a checkbox in the web UI)
drops those recipes entirely. Strict trades recall for the ability to say the
answer is complete.

### Cooking time

Taken from an explicit label where the book gives one (`Total time: 45
minutes`, `Prep 15 / Cook 30`), otherwise summed from durations in the method,
otherwise estimated from the number of steps. Results say which, and
`--timed-only` restricts to the trustworthy ones. Overnight marinades and
proves are flagged separately rather than added to the total, so a 20-minute
recipe that rests overnight doesn't get filed under "12 hours".

---

## How ranking works

For each recipe: `coverage` is the share of non-staple ingredients you have,
`missing` is how many you don't. Missing items are penalised steeply enough
that a recipe you can cook right now always beats one you can nearly cook, but
gently enough that "you're one onion away" still surfaces.

If nothing matches exactly, the search widens the shopping list rather than
showing an empty screen, and tells you it did.

Recipes appearing in several books — the same title in EPUB and MOBI, or two
editions — are collapsed to one result.

---

## Scale

Measured on this machine (M-series Mac, 8 workers) against a generated corpus
of 500 books × 100 recipes:

| | |
|---|---|
| Books indexed | 500 |
| Recipes extracted | 50,000 |
| Ingredient links | 713,605 |
| Recipes tagged with a meal | 50,000 (100%) |
| Recipes tagged with a cuisine | 33,878 (68%) |
| Index time | 81 s (162 ms/book, ~620 recipes/s) |
| Database size | 107 MB |
| Meal, cuisine, diet or allergen filter | 26–31 ms median |
| Diet + allergen + meal combined | 39 ms median |
| Pantry query | 70 ms median |
| Pantry + diet + allergen | 46 ms median |
| Browse / text query | 8–10 ms median |

Reproduce with `python tests/scale_test.py 500 100 /tmp/pantry-chef-scale`.

A pantry query that finds nothing costs more than one that succeeds, because
the search then retries at a wider tolerance rather than showing an empty
screen — worst case around 180 ms for three passes.

Scaling further is mostly linear: indexing is CPU-bound and parallel, and the
query cost is driven by the ingredient index rather than the recipe count — a
pantry query stayed at ~70 ms whether it returned 0 results or 40. A library
several times this size stays well inside interactive latency; beyond that the
next step would be a covering index on `recipe_ingredients` or moving the hot
join into a materialised per-ingredient posting list.

Re-indexing is incremental. Files are hashed, and unchanged books are skipped,
so adding ten books to a 500-book library costs ten books of work.

---

## Formats

| Format | Reader | Notes |
|---|---|---|
| `.epub`, `.kepub` | standard library | Zip + OPF spine. No dependency. Photos included. |
| `.pdf` | PyMuPDF | Text PDFs, photos included. **Scanned PDFs need OCR first** — there is no text layer to read. |
| `.mobi`, `.azw`, `.azw3`, `.prc` | `mobi` | Unpacked to EPUB/HTML, then read normally. No photos: the unpacked copy is temporary, so there is nothing to point at later. |

DRM-protected files cannot be read, by any of these. Strip DRM from books you
own before indexing, or they'll be reported as failures.

---

## Limitations

Worth knowing before you trust a result:

- **Extraction is heuristic, not perfect.** Well-structured books do very well;
  loosely typeset ones lose the occasional title or run two short recipes
  together. Every recipe carries a `confidence` score, and anything below 0.4
  is discarded by default (`--min-confidence` to tune).
- **The ingredient lexicon is English** and weighted towards European and
  American cooking. Extending it is just editing `pantry_chef/parse/lexicon.py` —
  the vocabulary is plain data, no code changes needed.
- **Kindle formats are wired up but untested against real files.** The EPUB and
  PDF paths are covered by tests; the MOBI path depends on the `mobi` package
  behaving as documented.
- **Ingredient matching is generous.** Typing `chicken` matches `chicken thigh`
  and `chicken breast`. That's usually what you want, but it means a match is
  not a guarantee the recipe will work with exactly what's in your fridge.
- **Quantities are parsed but not compared.** The search knows you have flour,
  not that you have 200g of it.
- **Converted amounts are rounded** to what a cook would measure, so they are
  close rather than exact. For baking, where a few grams matter, the original
  units are one click away.
- **Recipe ids change when you re-index with `--force`**, because recipes are
  replaced rather than updated. Bookmarked recipe links will not survive a full
  re-read of the library.
- **Photos are matched by position**, being the nearest image to the recipe's
  title inside its own span. In books that place photography on facing pages
  this is right; in books that group photos into plates it can pair the wrong
  picture with a dish.
- **Moving or deleting a book breaks its photos**, since they are read from the
  file on demand. The recipe text survives; the image quietly disappears.
- **Meal and cuisine are guesses.** A book with no chapter structure and plain
  dish names gives the classifier little to work with, and it will leave
  cuisine blank rather than invent one. Both are plain data tables in
  `pantry_chef/parse/classify.py`, so correcting or extending them is editing a
  dict.
- **A database built before tagging needs `pantry-chef index --force` once** to add
  meal, cuisine, diet and allergen tags. `pantry-chef stats` says so if yours does.
  Older databases are otherwise migrated in place on first open — columns are
  added and the search index rebuilt without losing anything.

---

## Testing

```bash
.venv/bin/python -m pytest -q
```

211 tests covering quantity parsing, ingredient canonicalisation, time
extraction, meal and cuisine classification, allergen and diet derivation,
metric conversion, title casing and step splitting, photo extraction and
downscaling, block parsing, segmentation
across all three book shapes, ingest, resumability, schema migration, failure
isolation and diagnosis, background indexing jobs, search ranking, every filter,
and the HTTP API end to end.

The fixtures are generated, not shipped: `tests/make_fixtures.py` builds the
same five recipes as a well-structured EPUB, a loosely-structured EPUB and a
markup-free PDF, so the segmenter is tested against all three shapes it will
meet in a real library.

---

## Layout

```
pantry_chef/
  extract/        file formats -> ordered text blocks
    blocks.py       the Block model and the HTML parser
    epub.py         EPUB/KEPUB via zipfile + OPF spine
    pdf.py          PDF via PyMuPDF, headings from font size
    mobi.py         Kindle formats via the mobi package
  parse/          blocks -> recipes
    lexicon.py      the vocabulary: units, ingredients, synonyms, staples
    quantities.py   "1½ cups" -> (1.5, cup)
    ingredients.py  "4 spring onions, sliced" -> scallion
    timing.py       "Prep 15 / Cook 30" -> 45 minutes
    classify.py     meal type, cuisine and difficulty
    diet.py         allergens and diets
    metric.py       imperial -> metric, ingredient-aware
    segment.py      find the recipes in a book
  db.py           schema and storage
  index.py        parallel, resumable ingest
  jobs.py         background indexing runs and failure diagnosis
  search.py       pantry matching and ranking
  images.py       recipe photos, read from the book and cached
  cli.py          command line
  web/            FastAPI app, search page, library page, recipe card
    static/       stylesheet, pages, and the logo (mark, favicon, lockup)
```
