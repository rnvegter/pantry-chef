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
| **[D. Docker](#d-docker)** | Docker is already there, or you want no Python on the machine | image built and run: 257 MB; Docker CLI itself untested |
| **[E. Podman](#e-podman)** | you want the container rootless and daemon-free | built and run end to end |

Putting it on a server that stays on — with a reverse proxy, HTTPS and a
password in front — is a different job, covered in **[DEPLOY.md](DEPLOY.md)**.

On *status*: routes A and E were both run end to end — A installed from scratch,
E built as an image and used to index and serve a real library. D uses that same
image and the same compose file, which were exercised, but through Podman rather
than Docker. B and C are thin wrappers over the same standard Python packaging,
verified independently: a non-editable install into a clean environment serves
the pages, static assets, API and recipe photos correctly.

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

## D. Docker

Everything below is copy-paste in order. Docker runs the app in a container, so
Python is never installed on your machine.

> **Status.** Everything from step D3 onwards was exercised against this exact
> `compose.yaml` — the `.env` file supplying `BOOKS`, the read-only book mount
> (a write into `/books` is refused), `run … index` putting 586 recipes where
> the running service reads them, and `ps` / `restart` / `stop` / `up` / `down`
> behaving as described, with the index surviving a stop and a `down`.
>
> That was run with `podman-compose`, which reads the same file. **The `docker`
> CLI itself was not run**: this was written on a machine with Podman and no
> Docker, so step D1 and the exact `docker` spellings are from the documentation
> rather than from a terminal. `docker compose config` validates the file before
> you start anything.

### D1. Install Docker

**macOS**

```bash
brew install --cask docker
open -a Docker
```

Wait for the whale icon in the menu bar to stop animating, then check it:

```bash
docker --version && docker compose version
```

**Linux (Debian, Ubuntu, Fedora)**

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

Log out and back in for the group change to apply, then check it:

```bash
docker --version && docker compose version
```

> Adding yourself to the `docker` group grants root-equivalent access to that
> machine. On a shared box, either keep using `sudo docker`, or use
> [route E](#e-podman), which is rootless.

**Windows** — install
[Docker Desktop](https://www.docker.com/products/docker-desktop/) with the WSL 2
backend, then run the commands below from a WSL terminal.

Compose v2 ships with all of these, so the command is `docker compose`, with a
space — not the older `docker-compose`.

### D2. Get the app

```bash
git clone https://github.com/rnvegter/pantry-chef.git
cd pantry-chef
```

### D3. Point it at your cookbooks

This is the step that catches people out, so it is worth being precise about
what is happening.

A container cannot see your disk. Your cookbook folder has to be *mapped* into
it — handed through under a fixed name that the app looks for:

| | |
|---|---|
| **On your machine** | wherever your books actually live, e.g. `/Users/you/Books/Cookbooks` |
| ⇩ mapped to ⇩ | |
| **Inside the container** | always `/books`, read-only |

**Only the left-hand side is yours to change.** `/books` is the name the app is
told to look in; every command in this guide says `/books` and none of them
should be edited.

Set your side of the mapping in a `.env` file, which Compose reads
automatically:

```bash
echo "BOOKS=$HOME/Books/Cookbooks" > .env
```

Use the real, absolute path to the folder that holds your ebooks:

```bash
# macOS / Linux — a folder in your home directory
echo "BOOKS=$HOME/Books/Cookbooks" > .env

# macOS — an external drive
echo "BOOKS=/Volumes/Media/Cookbooks" > .env

# Linux server
echo "BOOKS=/srv/cookbooks" > .env

# Windows, from a WSL terminal
echo "BOOKS=/mnt/c/Users/You/Documents/Cookbooks" > .env
```

If the path contains spaces, quote it — `BOOKS="/Users/you/My Books/Cookbooks"`.
Do not put a trailing slash on it.

**Point at the top folder, not at each book.** Sub-folders are searched too, so
a library organised by author or by shelf works as it is:

```
Cookbooks/                 ← point BOOKS at this
├── Baking/
│   └── The Scone Queen Bakes.epub
├── Ottolenghi/
│   └── Simple.epub
└── Dont Think About Dinner.epub
```

`.epub`, `.kepub`, `.pdf`, `.mobi`, `.azw`, `.azw3` and `.prc` are picked up.
Anything else is ignored, as are hidden folders, so the `.caltrash` and
`.DS_Store` clutter a Calibre library leaves behind causes no trouble.

**Check the mapping before you go further.** This lists what the container can
actually see:

```bash
docker compose run --rm pantry-chef ls /books
```

Your books should be listed. If instead you get nothing back, the mapping is
wrong — see the table below before continuing, because indexing an empty folder
looks like success and produces an empty library.

| What you see | What it means | Fix |
|---|---|---|
| Your books, listed | The mapping works | Carry on to D4 |
| Nothing listed | The path does not exist. Docker quietly *creates* a missing bind-mount source as an empty folder rather than complaining, which is precisely how you end up with an empty library and no error | Run `cat .env`, then `ls` that exact path on your own machine |
| `no such file or directory` | Same cause, said out loud — Podman refuses instead of inventing the folder | As above |
| Nothing listed, but the path is definitely right | macOS or Windows: the folder is outside the area shared with Docker's VM | Add it under Docker Desktop → Settings → Resources → File sharing |
| `Permission denied` | The folder is not readable by other users | `chmod -R a+r /path/to/books` |
| Some books listed, others not | The missing ones are in an unsupported format | Convert them with Calibre |

After changing `.env`, recreate the container so it picks the new path up —
editing the file alone does not move a running mount:

```bash
docker compose up -d --force-recreate
```

> **macOS and Windows only.** Docker runs Linux in a VM, and only shared paths
> reach it. Your home directory is shared by default, so `~/Books/…` works out
> of the box. A path outside it — an external drive, another volume — must be
> added first in Docker Desktop under **Settings → Resources → File sharing**,
> otherwise `/books` comes back empty however correct `.env` looks.

### D4. Build and start it

```bash
docker compose up -d
```

The first run builds the image, which takes well under a minute. Check it came
up:

```bash
docker compose ps
```

### D5. Build the index

**Do this through Compose, not with a bare `docker run`.** Compose prefixes
volume names with the project directory, so the running service reads
`pantry-chef_pantry-chef-data`; a bare `docker run -v pantry-chef-data:/data`
would write to a *different* volume and the app would report no database at all.

```bash
docker compose run --rm pantry-chef pantry-chef index /books
```

This is the slow part, and you only do it once — roughly 150 ms per book. Check
the result:

```bash
docker compose run --rm pantry-chef pantry-chef stats
```

### D6. Open it

**http://127.0.0.1:8077** — search is at `/`, the library manager at `/library`.

The port is bound to `127.0.0.1` deliberately. **Pantry Chef has no
authentication**; it assumes only you can reach it. To put it on a network,
follow [DEPLOY.md](DEPLOY.md), which puts a password in front of it.

### D7. Day to day

```bash
docker compose logs -f                  # follow the log
docker compose restart                  # restart it
docker compose stop                     # stop it (the index is kept)
docker compose up -d                    # start it again
```

Add books later — drop them in the folder, then re-index. Unchanged books are
skipped, so this is cheap to re-run:

```bash
docker compose run --rm pantry-chef pantry-chef index /books
```

Or use the **Library** page in the browser, which does the same thing with a
progress bar.

### D8. Upgrade

```bash
cd pantry-chef
git pull
docker compose build --pull
docker compose up -d
```

`--pull` also refreshes the Python base image, so you pick up its security
updates rather than rebuilding on a stale layer.

If an upgrade changes how recipes are parsed, re-read your books to get the
improvements:

```bash
docker compose run --rm pantry-chef pantry-chef index /books --force
```

Older databases are migrated in place on first open — columns are added and the
search index rebuilt without losing anything. One caveat: **`--force` reassigns
recipe ids**, so bookmarked `/recipe/<id>` links will not survive it.

Old images pile up after a few upgrades. To reclaim the space:

```bash
docker image prune
```

### D9. Uninstall

```bash
docker compose down                     # stop and remove the container
docker compose down -v                  # …and delete the index as well
docker rmi pantry-chef                  # remove the image
```

Your cookbooks are never touched by any of this — they are only ever mounted
read-only.

---

## E. Podman

The same image, rootless and without a background daemon. Podman reads the same
`Containerfile` and `compose.yaml`; only the commands differ.

### E1. Install Podman

**macOS**

```bash
brew install podman
podman machine init
podman machine start
```

If your books live outside your home directory, share that path with the VM when
you create it: `podman machine init -v /Volumes/Books:/Volumes/Books`.

**Linux**

```bash
sudo apt install -y podman        # Debian/Ubuntu
sudo dnf install -y podman        # Fedora/RHEL
```

### E2. Get the app and build the image

```bash
git clone https://github.com/rnvegter/pantry-chef.git
cd pantry-chef
podman build -t pantry-chef -f Containerfile .
```

### E3. Point it at your cookbooks

Podman takes the mapping on the command line rather than from a file. The `-v`
flag reads **`your folder`** `:` **`the name inside`** `:` **`options`**:

```
-v ~/Books/Cookbooks:/books:ro
   └────────┬───────┘ └──┬─┘ └┬┘
    your books      always  read-only
                    /books
```

Change only the first part. `/books` is the name the app is told to look in, and
`ro` is what guarantees the container can never modify your library.

Point it at the folder that *holds* your books, not at individual files —
sub-folders are searched too, so a library organised by author works as it is.
`.epub`, `.kepub`, `.pdf`, `.mobi`, `.azw`, `.azw3` and `.prc` are picked up;
hidden folders are skipped.

Check the mapping before indexing, because an empty folder indexes "successfully"
into an empty library:

```bash
podman run --rm -v ~/Books/Cookbooks:/books:ro pantry-chef ls /books
```

Your books should be listed. Nothing listed means the path is wrong, or — on
macOS — that it is outside the directory shared with the Podman VM; see E1.

### E4. Build the index

```bash
podman run --rm \
  -v ~/Books/Cookbooks:/books:ro \
  -v pantry-chef-data:/data \
  pantry-chef pantry-chef index /books
```

Add `,Z` to the books mount on SELinux systems (Fedora, RHEL):
`-v ~/Books/Cookbooks:/books:ro,Z`.

### E5. Run it

```bash
podman run -d --name pantry-chef \
  -p 127.0.0.1:8077:8077 \
  -v ~/Books/Cookbooks:/books:ro \
  -v pantry-chef-data:/data \
  pantry-chef
```

Then open **http://127.0.0.1:8077**.

### E6. Day to day and upgrading

```bash
podman logs -f pantry-chef              # follow the log
podman stop pantry-chef                 # stop it
podman start pantry-chef                # start it again

# add books later
podman run --rm -v ~/Books/Cookbooks:/books:ro -v pantry-chef-data:/data \
  pantry-chef pantry-chef index /books

# upgrade
git pull
podman build --pull -t pantry-chef -f Containerfile .
podman rm -f pantry-chef
podman run -d --name pantry-chef -p 127.0.0.1:8077:8077 \
  -v ~/Books/Cookbooks:/books:ro -v pantry-chef-data:/data pantry-chef

# uninstall
podman rm -f pantry-chef
podman rmi pantry-chef
podman volume rm pantry-chef-data       # deletes the index
```

Podman can also use `compose.yaml` — install a provider with
`pip install podman-compose`, then follow the Docker commands in
[route D](#d-docker), substituting `podman-compose` for `docker compose`.

### Notes for both container routes

**Pick one home and stay there.** The database records where each book lives.
Index inside a container and the paths are `/books/…`; index on the host and
they are `/Users/you/…`. Those paths are what recipe photos are read from at
display time, so a database built in one place shows no photos in the other, and
re-indexing treats every book as new. Switching costs one `index --force`.

**Why the mounts are what they are.**

| Mount | Why |
|---|---|
| `/books` **read-only** | Your library is never copied into the image and never modified. It must stay mounted while the app runs, because recipe photos are read from the books on demand. |
| `/data` | The index and the photo cache. Without it, every restart re-reads your whole library. |

**Rootless file ownership (Podman).** The container runs as UID 1000. A *named*
volume is initialised with the right ownership automatically; if you bind-mount
a host directory at `/data` instead, add `:U` so Podman adjusts it:
`-v ./data:/data:U`.

**Is a container worth it here?** On Linux, yes — it isolates the dependencies
cleanly. On macOS it is a harder sell: both Docker and Podman put a VM between
the app and your books, file access across that boundary is slower, and route A
has none of those problems. Containers earn their place when you want Pantry
Chef on a home server or NAS and reach it from a laptop.

---

## Index your cookbooks

*Routes A, B and C. The container routes have their own commands above.*

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

Inside a container instead:

```bash
docker compose run --rm pantry-chef sh -c "pip install pytest && pytest -q"
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
```

Container routes upgrade differently — see [D8](#d8-upgrade) for Docker and
[E6](#e6-day-to-day-and-upgrading) for Podman.

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
| Docker | `docker compose down -v && docker rmi pantry-chef` |
| Podman | `podman rm -f pantry-chef && podman rmi pantry-chef && podman volume rm pantry-chef-data` |

No background service is registered on any route, and your ebooks are untouched.
