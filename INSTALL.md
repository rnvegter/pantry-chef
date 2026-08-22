# Installing Pantry Chef

Everything runs locally. There is no account, no API key and no network call —
your cookbooks and the index built from them never leave your machine.

---

## Requirements

| | |
|---|---|
| **Python** | 3.11 or newer (`python3 --version`) |
| **Disk** | ~200 MB for a 500-book library, plus a photo cache that grows as you browse |
| **OS** | macOS, Linux or Windows |

Nothing else. SQLite ships with Python, and EPUB parsing uses only the standard
library.

---

## 1. Get the code

```bash
git clone https://github.com/rnvegter/pantry-chef.git
cd pantry-chef
```

## 2. Create a virtual environment

A virtual environment keeps these dependencies out of your system Python.

```bash
python3 -m venv .venv
```

On **macOS and Linux**:

```bash
source .venv/bin/activate
```

On **Windows** (PowerShell):

```powershell
.venv\Scripts\Activate.ps1
```

## 3. Install

```bash
pip install -e ".[all]"
```

`[all]` adds PDF and Kindle support. If you only have EPUB books you can install
the bare package instead — EPUB needs no third-party library at all:

| Command | What you get |
|---|---|
| `pip install -e .` | EPUB and KEPUB only |
| `pip install -e ".[pdf]"` | adds PDF (PyMuPDF) |
| `pip install -e ".[kindle]"` | adds MOBI / AZW / AZW3 |
| `pip install -e ".[all]"` | all formats |
| `pip install -e ".[all,dev]"` | all formats plus the test suite |

> **Note on the examples below.** They assume you have activated the virtual
> environment, so `python` means the one inside `.venv`. If you would rather not
> activate it, replace `python` with `.venv/bin/python` (or
> `.venv\Scripts\python.exe` on Windows) everywhere.

---

## 4. Index your cookbooks

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

## 5. Start the app

```bash
python -m pantry_chef serve
```

Then open **http://127.0.0.1:8077**. Search is at `/`, and the library manager
— where you add folders and watch indexing happen — is at `/library`.

If you would rather add folders through the interface than the command line,
skip step 4 entirely: start the app, go to **Library**, and add a folder there.

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

## Running in a container (Podman or Docker)

> **Status: written, not yet run.** The Python packaging underneath it is
> verified — a non-editable install serves the pages, static assets, API and
> recipe photos correctly, which is exactly what the image does. What has not
> been exercised is the Linux layer: the base image, the apt packages and the
> volume permissions. Treat the first build as a trial.

A `Containerfile` and a `compose.yaml` are included. Podman and Docker both read
them; the commands below use `podman`, and `docker` works identically.

### Before you start: pick one home and stay there

**The database records where each book lives.** Index inside the container and
the paths are `/books/…`; index on the host and they are `/Users/you/…`. Those
paths are what recipe photos are read from at display time, so a database built
in one place will show no photos in the other, and re-indexing will treat every
book as new.

Choose the container *or* the host, and keep to it. Switching means one
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

Or with compose:

```bash
BOOKS=~/Books/Cookbooks podman compose up -d
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
boundary is slower, and the venv install in step 3 has none of those problems.
The container earns its place if you want to run Pantry Chef on a home server or
NAS and reach it from a laptop.

---

## Verifying the install

```bash
pip install -e ".[all,dev]"
pytest -q
```

213 tests should pass in about ten seconds. They generate their own sample
cookbooks, so you do not need any books of your own to run them.

---

## Troubleshooting

**`command not found: python3`**
Install Python 3.11+ from [python.org](https://www.python.org/downloads/), or
`brew install python` on macOS.

**A book fails with "File is not a zip file"**
It is DRM-protected, or the download was truncated. DRM cannot be read by any of
these parsers. Remove the DRM from your own copy first (Calibre with the DeDRM
plugin is the usual route), or re-download the file.

**A PDF indexes but yields no recipes**
It is almost certainly a scan: the pages are images, so there is no text to
extract. Run OCR over it — macOS Preview, Acrobat, or
[`ocrmypdf`](https://github.com/ocrmypdf/OCRmyPDF) — and index it again.

**"install pymupdf to index PDF cookbooks"**
The optional PDF dependency is missing: `pip install pymupdf`.

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
pip install -e ".[all]"
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

Delete the folder. Nothing is installed system-wide, no background service is
registered, and your ebooks are untouched.
