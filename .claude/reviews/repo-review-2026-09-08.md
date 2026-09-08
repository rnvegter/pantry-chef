# Code Review: pantry-chef

**Reviewed**: 2026-09-08
**Target**: https://github.com/rnvegter/pantry-chef (main @ ceb7e31)
**Mode**: Repository review — the input was a repo URL, not a PR, and the
working tree is clean, so there were no uncommitted changes to review.
**Decision**: **BLOCK** — one CRITICAL chain had to be fixed before this was
exposed to any network.

**Status**: all CRITICAL, HIGH and MEDIUM findings are fixed and verified; see
*Resolution* at the end. The three LOW findings remain open by choice.

## Summary

The application is well tested (246 passing) and the parsing layer is sound.
The problem is entirely at the web boundary: untrusted ebook text reaches
`innerHTML` unescaped, and the origin it executes in offers unauthenticated
filesystem enumeration. Those two combine into a real, demonstrated exploit,
and `DEPLOY.md` currently tells the reader the opposite.

## Findings

### CRITICAL

**C1 — Stored XSS from an indexed cookbook, chained to filesystem enumeration**

`pantry_chef/web/static/index.html:598,601` and `:370` interpolate ebook-derived
strings into `innerHTML` without escaping:

```js
598  `<div class="need">Ingredients: <b>${r.ingredients.map(m => m.display).join(", ")}</b></div>`
601  `<div class="need">Shopping list: <b>${r.missing.map(m => m.display).join(", ")}</b></div>`
370  row.innerHTML = `<span>${item.name}</span>...`      // ingredient autocomplete
```

`display` and `canonical` are parsed straight out of the book's ingredient
lines. Note the same file escapes correctly at `:454` in the newer by-name
autocomplete, so this is an inconsistency, not a considered decision.

*Demonstrated, not theorised.* An EPUB with an entity-encoded ingredient line:

```
<li>2 cups flour &lt;img src=x onerror="window.__XSS_FIRED=1"&gt;</li>
```

survives parsing (the entities decode to literal angle brackets *after* the
block parser has run, so the parser's own tag-stripping does not help) and
reaches the API as:

```
display: 'flour <img src=x onerror="window.__xss_fired=1">'
```

Rendering that through the real `card()` function injected a live `<img>` node
and fired `onerror` — confirmed in the browser.

**The escalation is what makes this critical.** The origin that JavaScript runs
in offers, with no authentication:

| Endpoint | Verified result |
|---|---|
| `GET /api/library/browse?path=/` | lists `/` — 16 directories |
| `GET /api/library/browse?path=/etc` | lists `/etc` — 19 directories |
| `GET /api/library/browse?path=$HOME` | lists the home directory — 20 directories |
| `POST /api/library/inspect {"path": "~/.ssh"}` | `ok: true` — confirms the path exists |

No CSP and no egress restriction, so a payload can walk the filesystem and POST
the results anywhere. The precondition — indexing a cookbook downloaded from
the internet — is the application's entire purpose.

**Fix**: escape at the three sinks (`esc()` already exists in the file), and add
a CSP (`default-src 'self'`) so a missed sink cannot execute. Treat every book-
derived string as hostile: it is the one input the app cannot vouch for.

### HIGH

**H1 — Unauthenticated filesystem enumeration, independent of the XSS**

`pantry_chef/jobs.py:list_directories` / `inspect_folder`, exposed at
`/api/library/browse` and `/api/library/inspect`, accept an arbitrary absolute
path and are reachable by anyone who can reach the port. `DEPLOY.md` walks the
reader through putting this behind a single shared basic-auth password; one
credential then yields directory listings of the whole host.

**Fix**: confine both to the registered source folders plus the user's home, or
gate the folder picker behind an explicit opt-in flag that a server deployment
turns off.

**H2 — `DEPLOY.md` states a threat model the code contradicts**

`DEPLOY.md:23-25`: *"There is otherwise little to attack: no user accounts, no
uploads, no shell-outs, and the app only ever reads your books. The exposure is
your library, not your machine."*

The last sentence is false, per the table in C1. A reader making a deployment
decision on that sentence is being misled by the project's own documentation.
Documentation that understates risk is a security defect, not a docs nit.

**Fix**: correct the paragraph once H1 is fixed, and say plainly what the folder
picker exposes.

### MEDIUM

- **M1 — No security headers.** No CSP, `X-Content-Type-Options`, or
  `X-Frame-Options` on any response. A CSP would have contained C1.
  (`pantry_chef/web/app.py`)
- **M2 — Nine `except Exception` handlers** (`jobs.py`, `images.py`,
  `extract/*.py`). Several are deliberate and commented — a malformed book
  should not kill a 500-book run — but `images.py:downscale` and
  `_from_pdf` swallow everything silently, so a systematic failure looks
  identical to "this book has no photo".
- **M3 — Column name interpolated into SQL.** `db.py:403`
  `f"SELECT b.{column} ..."` in `_complete_book_column`. Currently unreachable
  from user input (called only with the literals `"author"` and `"title"`), so
  not exploitable today — but it is an injection-shaped API one careless call
  away from being real. Take an enum, or map through a whitelist dict.
- **M4 — No linter or type checker.** `pyproject.toml` configures pytest only.
  For ~6,100 lines with no static analysis, `ruff` and `mypy` would be cheap.

### LOW

- **L1 — `_build_filters` is 134 lines** (`search.py:181`), well past the
  50-line guideline. It is cohesive — one WHERE clause assembled in stages —
  but the facet work has already pushed it once.
- **L2 — `index.html` is 748 lines** of markup, CSS and JS in one file. Fine at
  this size; splitting the script out would help before it grows again.
- **L3 — Magic numbers** without named constants: the 160 ms autocomplete
  debounce, the 120 ms blur close, `limit=200` for browse.

## Validation Results

| Check | Result |
|---|---|
| Type check | Skipped — no mypy/pyright configured (see M4) |
| Lint | Skipped — no ruff/flake8 configured (see M4) |
| Tests | **Pass** — 246 passed, 1 skipped |
| Build | **Pass** — package installs cleanly |

## Files Reviewed

Modified/added across the reviewed surface:

- `pantry_chef/web/static/index.html` — C1, L2, L3
- `pantry_chef/web/app.py` — M1
- `pantry_chef/jobs.py` — H1, M2
- `pantry_chef/db.py` — M3
- `pantry_chef/search.py` — L1
- `pantry_chef/images.py`, `pantry_chef/extract/*.py` — M2
- `DEPLOY.md` — H2
- `pyproject.toml` — M4

## Note on publishing

The input was a repository URL with no PR, so there is no GitHub review to post
to. Nothing was pushed as part of this review.


---

## Resolution

Fixed and verified after the review:

**C1 — stored XSS.** All five book-derived interpolations now pass through
`esc()` (`search.js`). Re-running the original proof against the live page:
`injectedImgNodes: 0`, `onerrorFired: false`, and the payload renders as
visible text.

**M1 — no CSP.** A `Content-Security-Policy` is now set on every response.
Making it meaningful required moving the page scripts out of the HTML into
`search.js`, `library.js` and `recipe.js`, because `script-src 'self'` only
stops an injected event handler when `'unsafe-inline'` is absent — and that
excludes our own inline blocks too. The single inline `onerror` in the recipe
hero moved into script. Verified: injecting `<img src=x onerror=…>` directly,
bypassing `esc()` entirely, is now blocked by the browser.

**H1 — filesystem enumeration.** `browse_roots()` / `is_browsable()` confine the
picker to the home directory, already-registered sources, and
`$PANTRY_CHEF_BROWSE_ROOTS`. Paths are resolved before comparison, so a
`../../etc` traversal is refused. Enforced on `browse`, `inspect` *and*
`sources` — adding a source must not be the way around the limit. Verified:
`/etc` and `/` refused, home still lists.

**H2 — the false claim in DEPLOY.md.** Corrected in place, and the correction
says what the old text got wrong rather than quietly deleting it. The systemd
unit, Quadlet file and `compose.yaml` now set `PANTRY_CHEF_BROWSE_ROOTS`.

**Regression cover.** 253 tests pass, 7 of them new:

- a source guard asserting no book-derived field reaches the page unescaped —
  confirmed to fail when either escape is removed
- an end-to-end test that a poisoned EPUB reaches the API with its payload
  intact, pinning what the guard defends against
- CSP and `nosniff` headers present, including on error responses
- pages load external scripts and contain no inline `<script>`
- browsing refuses `/etc`, `/` and traversal, and still allows home

### Second pass — the MEDIUM findings

**M2 — silent swallows.** Five of the nine blind excepts already surfaced their
error to the user or re-raised, and were left alone. The four that vanished now
log: `images.py` at warning (a photo failing is worth knowing about), the
per-image PDF loop and the malformed-markup path at debug (high volume, low
signal). Each remaining blind except carries `# noqa: BLE001` and the reason,
so the rule stays on and every exemption is a stated decision.

**M3 — column interpolated into SQL.** `_complete_book_column` now resolves the
column through a whitelist and raises `ValueError` on anything else. Tested with
`"title FROM books; DROP TABLE recipes--"`.

**M4 — no linter or type checker.** `ruff` and `mypy` are configured in
`pyproject.toml` and added to the `dev` extra. Both are clean.

Working the lint list down turned up two things worth recording:

1. **`ruff --fix` damaged the code.** SIM905 rewrote five multi-line word lists
   in `lexicon.py` into single-line literals, one of them 4,353 characters — in
   the file a person edits by hand most often. Reverted, and SIM905 is now
   ignored with that reason written down. Autofixes were reviewed rather than
   trusted after that.
2. **SIM118 would have introduced a bug.** It flagged `"col" in row.keys()` on
   a `sqlite3.Row`, suggesting `"col" in row` — but `in` on a Row searches its
   *values*, so the checks would silently have become False and three fields
   would have fallen back to defaults on a database that has the columns. Kept,
   with a `noqa` explaining why.

mypy also found a latent bug rather than only style: `int(cursor.lastrowid)` in
`insert_recipes`, where `lastrowid` is `int | None`, would raise `TypeError`
rather than anything diagnosable if an insert ever returned no row id. It now
raises a named error.

## Still open

L1 (`_build_filters` at 134 lines), L2 (`index.html` size — reduced by moving
the script out, but still large), and L3 (magic numbers). All style, none
behavioural, and left deliberately.
