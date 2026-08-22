# Installing Pantry Chef

Everything runs locally. There is no account, no API key and no network call —
your cookbooks and the index built from them never leave your machine.

---

## Requirements

| | |
|---|---|
| **Python** | 3.11 or newer (`python3 --version`) — not needed for the container route |
| **Disk** | ~200 MB for a 500-book library, plus a photo cache that grows as you browse |
| **OS** | macOS, Linux or Windows |

Nothing else. SQLite ships with Python, and EPUB parsing uses only the standard
library.

---

## Choose an install route

Four ways in. They all end up at the same place — pick the one that suits how
you already work.

| Route | Best for | Status |
|---|---|---|
| **[A. venv + pip](#a-venv--pip)** | most people; the default | tested end to end |
| **[B. uv](#b-uv)** | you already use `uv` and want it fast | packaging verified, commands standard |
| **[C. pipx](#c-pipx)** | you only want the `pantry-chef` command | packaging verified, commands standard |
| **[D. Podman or Docker](#d-podman-or-docker)** | a home server or NAS, or isolation on Linux | built and run: 257 MB, indexes and serves |

Putting it on a server that stays on — with a reverse proxy, HTTPS and a
password in front — is a different job, covered in **[DEPLOY.md](DEPLOY.md)**.

On *status*: routes A and D were both run end to end — A installed from scratch,
D built as an image and used to index and serve a real library. B and C are thin
wrappers over the same standard Python packaging, which was verified
independently: a non-editable install into a clean environment serves the pages,
static assets, API and recipe photos correctly.

Every route needs the source first:

```bash
git clone https://github.com/rnvegter/pantry-chef.git
cd pantry-chef
```

---

## A. venv + pip

A virtual environment keeps these dependencies out of your system Python.

```bash
python3 -m venv .venv
```

Activate it — on **macOS and Linux**:

```bash
source .venv/bin/activate
```

On **Windows** (PowerShell):

```powershell
.venv\Scripts\Activate.ps1
```

Then install:

```bash
pip install -e ".[all]"
```

`[all]` adds PDF and Kindle support. If you only have EPUB books you can install
less — EPUB needs no third-party library at all:

| Command | What you get |
|---|---|
| `pip install -e .` | EPUB and KEPUB only |
| `pip install -e ".[pdf]"` | adds PDF (PyMuPDF) |
| `pip install -e ".[kindle]"` | adds MOBI / AZW / AZW3 |
| `pip install -e ".[all]"` | all formats |
| `pip install -e ".[all,dev]"` | all formats plus the test suite |

> **On the examples below.** They assume the virtual environment is activated,
> so `python` means the one inside `.venv`. If you would rather not activate it,
> use `.venv/bin/python` (or `.venv\Scripts\python.exe` on Windows) instead.

Now go to [Index your cookbooks](#index-your-cookbooks).

---

## B. uv

[uv](https://github.com/astral-sh/uv) is a fast drop-in replacement for `pip`
and `venv`. If you have it:

```bash
uv venv
uv pip install -e ".[all]"
```

Then either activate `.venv` as above, or prefix commands with `uv run`:

```bash
uv run pantry-chef index ~/Books/Cookbooks
uv run pantry-chef serve
```

The same extras apply — `".[pdf]"`, `".[kindle]"`, `".[all,dev]"`.

Now go to [Index your cookbooks](#index-your-cookbooks).

---

## C. pipx

[pipx](https://pipx.pypa.io/) installs a command-line application into its own
isolated environment and puts the command on your `PATH`. Good if you want
`pantry-chef` available everywhere and never intend to edit the code.

```bash
pipx install ".[all]"
```

Then the command works from any directory:

```bash
pantry-chef index ~/Books/Cookbooks
pantry-chef serve
```

Because there is no project directory to fall back on, **tell it where to keep
the database**, otherwise it lands in `data/` relative to wherever you happen to
be standing:

```bash
export PANTRY_CHEF_DB=~/.local/share/pantry-chef/pantry-chef.db
```

Put that line in your shell profile to make it permanent. To upgrade later,
`pipx install --force ".[all]"` from an updated clone.

Now go to [Index your cookbooks](#index-your-cookbooks).

---

## D. Podman or Docker

> **Status: built and run.** The image was built with Podman on arm64 and
> exercised against a real library: it indexed 6 cookbooks to 586 recipes with
> no failures, served every page and endpoint, and extracted recipe photographs
> live from the read-only book mount. The image is **257 MB**.
>
> Compose was exercised too, with `podman-compose` against the same
> `compose.yaml` that Docker reads. Docker itself was not run — there is none on
> the machine this was written on.

A `Containerfile` and a `compose.yaml` are included. Podman and Docker both read
them; the commands below use `podman`, and `docker` works identically. To put
this on a server rather than your own machine — with a reverse proxy, HTTPS and
a password in front — see **[DEPLOY.md](DEPLOY.md)**, which has a route written
out in full for each platform.

### First, pick one home and stay there

**The database records where each book lives.** Index inside the container and
the paths are `/books/…`; index on the host and they are `/Users/you/…`. Those
paths are what recipe photos are read from at display time, so a database built
in one place will show no photos in the other, and re-indexing will treat every
book as new.

Choose the container *or* the host, and keep to it. Switching costs one
`index --force` to rewrite the paths.

### Build

```bash
podman build -t pantry-chef -f Containerfile .
```

### Index your books

A one-off run, with the same volumes the server will use:

```bash
podman run --rm \
  -v ~/Books/Cookbooks:/books:ro \
  -v pantry-chef-data:/data \
  pantry-chef pantry-chef index /books
```

### Serve

```bash
podman run -d --name pantry-chef \
  -p 127.0.0.1:8077:8077 \
  -v ~/Books/Cookbooks:/books:ro \
  -v pantry-chef-data:/data \
  pantry-chef
```

Then open **http://127.0.0.1:8077**.

Or with Compose, which builds and runs in one step:

```bash
echo "BOOKS=$HOME/Books/Cookbooks" > .env
docker compose up -d                    # or: podman-compose up -d
```

Then build the index **through Compose**, so it lands in the volume the service
actually reads — Compose prefixes volume names with the project name, so a bare
`docker run -v pantry-chef-data:/data` would write somewhere else and the app
would report no database:

```bash
docker compose run --rm pantry-chef pantry-chef index /books
```

### Why the volumes are what they are

| Mount | Why |
|---|---|
| `/books` **read-only** | Your library is never copied into the image and never modified. It must stay mounted while the app runs, because recipe photos are read from the books on demand. |
| `/data` | The index and the photo cache. Without it, every restart re-reads your whole library. |

The port is bound to `127.0.0.1` deliberately. **Pantry Chef has no
authentication** — it assumes it is reachable only by you. Publishing it on
`0.0.0.0` exposes your library to anything that can reach the host.

### Platform notes

**macOS.** Podman runs Linux in a VM, so the book folder must be shared with
that VM. Recent Podman versions mount your home directory automatically; if your
books live elsewhere, add it when creating the machine:

```bash
podman machine init -v /Volumes/Books:/Volumes/Books
podman machine start
```

**SELinux (Fedora, RHEL).** Append `:Z` to bind mounts so the container may read
them: `-v ~/Books/Cookbooks:/books:ro,Z`.

**Rootless file ownership.** The container runs as UID 1000. A *named* volume
(as above, and as in `compose.yaml`) is initialised with the right ownership
automatically. If you bind-mount a host directory at `/data` instead, add `:U`
so Podman adjusts it: `-v ./data:/data:U`.

### Is a container worth it here?

On Linux, yes — it isolates the dependencies cleanly. On macOS it is a harder
sell: Podman adds a VM between the app and your books, file access across that
boundary is slower, and route A has none of those problems. The container earns
its place if you want to run Pantry Chef on a home server or NAS and reach it
from a laptop.

---

## Index your cookbooks

*Routes A, B and C. The container route has its own commands above.*

Point it at the folder your ebooks live in:

```bash
python -m pantry_chef index ~/Books/Cookbooks
```

This is the slow part, and you only do it once. Expect roughly **150 ms per
book** — a 500-book library takes a minute or two. Progress prints as it goes:

```
found 6 book(s)
[1/6] Weeknight Dinners.epub -> 132 recipe(s) in 3.0s
...
scanned 6 · indexed 6 · skipped 0 · failed 0 · 586 recipes in 3.4s
```

Re-running is cheap. Files are hashed, so unchanged books are skipped and only
new ones are read.

Check what you ended up with:

```bash
python -m pantry_chef stats
```

## Start the app

```bash
python -m pantry_chef serve
```

Then open **http://127.0.0.1:8077**. Search is at `/`, and the library manager —
where you add folders and watch indexing happen — is at `/library`.

If you would rather add folders through the interface than the command line,
skip the indexing step entirely: start the app, go to **Library**, and add a
folder there.

---

## Where your data lives

| Path | What it is | In git? |
|---|---|---|
| `data/pantry-chef.db` | the index: recipes, ingredients, tags | no |
| `data/image-cache/` | downscaled recipe photos, rebuilt on demand | no |

Both are safe to delete; re-indexing rebuilds them. Nothing else is written
outside the project folder, and **your ebooks are never copied or modified** —
they are only read.

To keep the database somewhere else:

```bash
python -m pantry_chef --db /path/to/recipes.db stats
```

or set it once:

```bash
export PANTRY_CHEF_DB=/path/to/recipes.db
```

---

## Verifying the install

```bash
pip install -e ".[all,dev]"     # or: uv pip install -e ".[all,dev]"
pytest -q
```

213 tests should pass in about ten seconds. They generate their own sample
cookbooks, so you do not need any books of your own to run them.

---

## Troubleshooting

**`command not found: python3`**
Install Python 3.11+ from [python.org](https://www.python.org/downloads/), or
`brew install python` on macOS.

**`command not found: pantry-chef`**
The virtual environment is not activated. Either activate it, or use
`python -m pantry_chef` instead — that works whenever the environment's Python
is the one running.

**A book fails with "File is not a zip file"**
It is DRM-protected, or the download was truncated. DRM cannot be read by any of
these parsers. Remove the DRM from your own copy first (Calibre with the DeDRM
plugin is the usual route), or re-download the file.

**A PDF indexes but yields no recipes**
It is almost certainly a scan: the pages are images, so there is no text to
extract. Run OCR over it — macOS Preview, Acrobat, or
[`ocrmypdf`](https://github.com/ocrmypdf/OCRmyPDF) — and index it again.

**"install pymupdf to index PDF cookbooks"**
The optional PDF dependency is missing: `pip install pymupdf`, or reinstall with
the `[all]` extra.

**Port 8077 is already in use**

```bash
python -m pantry_chef serve --port 9000
```

**Indexing seems to run several times over**
You are calling `ingest()` from your own script without an
`if __name__ == "__main__":` guard. Python re-imports the calling module in each
worker process, so its top-level code runs again. Put your code behind that
guard, or use the `pantry-chef` command, which already does.

**The library page shows a book with no recipes**
Open **Library → Needs attention**. Every failure there is explained in plain
language with the fix, and has a Retry button.

---

## Updating

```bash
git pull
pip install -e ".[all]"          # uv:   uv pip install -e ".[all]"
                                 # pipx: pipx install --force ".[all]"
                                 # container: podman build -t pantry-chef -f Containerfile .
```

If an update changes how recipes are parsed, re-read your books to pick up the
improvements:

```bash
python -m pantry_chef index ~/Books/Cookbooks --force
```

Older databases are migrated in place on first open — columns are added and the
search index rebuilt without losing anything. One caveat: **`--force` reassigns
recipe ids**, because recipes are replaced rather than updated, so bookmarked
`/recipe/<id>` links will not survive a full re-read.

---

## Uninstalling

| Route | How |
|---|---|
| venv + pip, uv | delete the folder — nothing is installed system-wide |
| pipx | `pipx uninstall pantry-chef`, then delete the folder |
| container | `podman rm -f pantry-chef && podman rmi pantry-chef && podman volume rm pantry-chef-data` |

No background service is registered on any route, and your ebooks are untouched.
