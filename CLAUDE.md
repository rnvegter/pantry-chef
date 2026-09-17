# Working agreements for Pantry Chef

Pantry Chef searches a cookbook library (EPUB, PDF, Kindle) by what is in the
kitchen and how much time there is. Python 3.14, FastAPI + uvicorn, SQLite
(WAL, FTS5), PyMuPDF, plain HTML/CSS/JS. Extraction is heuristic, with no AI
model; the rules are plain data in `pantry_chef/parse/`. README.md explains the
design; INSTALL.md and DEPLOY.md cover running it.

## Before every commit

Run the full gate from the repository root. All three must pass:

```bash
.venv/bin/ruff check .
.venv/bin/mypy pantry_chef
.venv/bin/pytest -q
```

A flaky test is a bug to explain, not a retry. Find the race or the
dependency, then fix the test or the code.

## Committing

- Commit and push each finished feature or fix to `main` once the gate passes:
  `git push -q origin main` (https://github.com/rnvegter/pantry-chef, private).
- Commit as: `git -c user.name="Niels Vegter" -c user.email="n.vegter@proton.me" commit`
- Write full commit messages: what changed and why, what was verified (and
  against what), bugs found along the way, and anything the user must do
  (for example `pantry-chef index <folder> --force`).
- **Never commit** `data/` (the user's index and photo cache), `.env`, or
  `tests/fixtures/` (generated; real cookbooks are copyrighted).
- Keep the test count in README.md ("N tests covering…") and INSTALL.md
  ("N tests should pass") current.

## Verifying

- **Test against the real library, not only fixtures.** The index is
  `data/pantry-chef.db`, built from `~/Downloads/Ebooks`. Measure a heuristic
  across the whole library (for example "99% of lines scale") and read the
  misses before calling it done.
- **Back up the database before any browser test that writes to it**, using
  SQLite's backup API, into the session scratchpad. Afterwards, remove test
  data (favourites, corrections, shopping-list entries) and confirm the index
  is as it was.
- **Check it in the browser** with the `pantry-chef` preview server (restart it
  after Python changes): desktop and phone widths, dark mode, console errors,
  reload and back-button behaviour. Screenshots can lag, so confirm state with
  JavaScript too.
- **Make sure a test asserts something.** Assert the precondition a loop
  depends on; check that a regression test fails without the fix.

## Rules that must not be broken

- **Book text is untrusted.** Every value from a book reaches the page through
  `esc()` or `textContent`, never raw into `innerHTML`.
  `test_book_text_is_never_interpolated_unescaped` enforces this. If it fires
  on safe code, restructure the code; never loosen the guard.
- **No inline scripts or event handlers.** The Content-Security-Policy forbids
  them; page scripts live in `pantry_chef/web/static/*.js`.
- **Nothing user-owned references `recipes.id`.** A `--force` re-index
  re-creates every recipe with new ids. Favourites, corrections and the
  shopping list are keyed as `(book_path, title_key, occurrence)` on the book's
  own title (`source_title`), resolved through `db.keyed_ids_sql`. Unresolved
  rows are kept and reported, never deleted.
- **Read-only connections never run the schema.** Readers check for newer
  tables and columns (`has_table`, `has_column`). Schema changes migrate
  existing databases in place.
- **One web worker.** The indexing job manager and the folder watcher are
  per-process.
- **Parsing logic lives in Python and is tested there** (scaling, timers,
  totals). The pages only render.
- **The app has no authentication.** Say so wherever exposure is discussed.
  Server setups keep port 8077 on loopback behind a password-protected proxy.
- **The folder picker stays confined** to home, added folders and
  `PANTRY_CHEF_BROWSE_ROOTS`.

## How the product behaves

- **Honest over impressive.** Mark estimates (`~`), say when an amount was not
  adjusted, show amounts side by side rather than inventing a total, and say
  when a browser feature is unavailable.
- **Metric first,** preferring the book's own printed figures over computed
  ones, with the original units one click away.
- **Nothing is removed behind the user's back.** Automatic indexing only adds;
  missing books keep their favourites, corrections and list entries.

## Scope and communication

- Reuse and refactor shared steps rather than duplicating them
  (`ingredients_from_lines`, `enrich`, `write_recipe_children`).
- If a bug turns up outside the current change, flag it as a separate task
  with a self-contained prompt. The prompt covers where the code is and the
  test commands, reproducible evidence from the real data, the consequences,
  the task and the tests to add, and the guardrails. Don't widen the change.
- State plainly what was not verified. Lead with the risk that matters.
- Offer a few distinct options when taste decides (design, naming). Give a
  recommendation when asked what to do next.
- Report in plain language: what changed, what was verified, what is worth
  knowing, and what is still open.
