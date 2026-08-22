# Running Pantry Chef on a server

For putting Pantry Chef on a box that stays on — a home server, a NAS, a VPS —
and reaching it from your laptop or phone. If you just want it on the machine in
front of you, use **[INSTALL.md](INSTALL.md)** instead.

---

## Read this first

> ### Pantry Chef has no authentication.
>
> None. There is no login, no password, no session, no API key. It was written
> to run on `localhost`, where the only person who can reach it is you.
>
> **Put something in front of it before it touches a network.** A reverse proxy
> with a password, or a private network, or both. If you expose port 8077
> directly, anyone who can route to that host can read your entire library,
> trigger re-indexing, and browse your books' photography.
>
> Every route below ends with that protection in place. Do not stop halfway.

There is otherwise little to attack: no user accounts, no uploads, no
shell-outs, and the app only ever reads your books. The exposure is your
library, not your machine. That is still worth protecting.

---

## What the server needs

| | |
|---|---|
| **OS** | any Linux with systemd; also fine on a NAS that runs containers |
| **RAM** | very little. A parser process peaks at **~40 MB**, measured against books up to 312 MB — EPUBs are streamed out of the zip rather than loaded whole. A 1 GB VPS is comfortable. |
| **Disk** | your books, plus ~200 MB of index per 500 books, plus a photo cache that grows as you browse |
| **Books** | reachable from the server: local disk, or an NFS/SMB mount |
| **A hostname** | a subdomain you control, e.g. `recipes.example.com`, pointed at the server |

---

## Choose a route

All three end in the same place: the app on `127.0.0.1:8077`, a reverse proxy
with a password in front, HTTPS, and a service that restarts on boot. Each is
written out in full — pick one and read only that section.

| Route | Platform | Pick it if |
|---|---|---|
| **[A. Python + systemd](#route-a--python--systemd)** | none | you want the least machinery. Nothing to build, nothing to pull. |
| **[B. Podman](#route-b--podman)** | Podman | you want isolation and a rootless daemon-free runtime. Quadlet makes it a native systemd unit. |
| **[C. Docker](#route-c--docker)** | Docker | Docker is already on the box, or you prefer Compose. |

**Recommended tools**, whichever route you take:

| Job | Use | Why |
|---|---|---|
| **Reverse proxy + HTTPS** | **[Caddy](https://caddyserver.com/)** | certificates automatic, config is six lines. Used in all three routes below. |
| Reverse proxy, alternative | [nginx](https://nginx.org/) | if you already run it — see [swapping in nginx](#swapping-in-nginx) |
| **Private access** | **[Tailscale](https://tailscale.com/)** | the best answer to the missing authentication: keep it off the public internet entirely |
| Backups | anything that copies one file | the index is a single SQLite file |

---

## Route A — Python + systemd

No containers. The app runs from a virtualenv under its own user, supervised by
systemd.

### A1. Install the prerequisites

Debian or Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv git curl debian-keyring debian-archive-keyring apt-transport-https
```

Fedora or RHEL:

```bash
sudo dnf install -y python3 git curl
```

### A2. Create the user and directories

```bash
sudo useradd --system --home /opt/pantry-chef --shell /usr/sbin/nologin pantry
sudo mkdir -p /opt/pantry-chef /var/lib/pantry-chef /srv/cookbooks
sudo chown pantry:pantry /opt/pantry-chef /var/lib/pantry-chef
```

### A3. Put your cookbooks on the server

```bash
# from your laptop
rsync -av ~/Books/Cookbooks/ user@server:/tmp/cookbooks/
# on the server
sudo mv /tmp/cookbooks/* /srv/cookbooks/
sudo chown -R root:root /srv/cookbooks
sudo chmod -R a+r /srv/cookbooks
```

The app only ever reads them, so they do not need to be writable.

### A4. Install the app

```bash
sudo -u pantry git clone https://github.com/rnvegter/pantry-chef.git /opt/pantry-chef
cd /opt/pantry-chef
sudo -u pantry python3 -m venv .venv
sudo -u pantry .venv/bin/pip install ".[all]"
```

### A5. Build the index

```bash
sudo -u pantry PANTRY_CHEF_DB=/var/lib/pantry-chef/pantry-chef.db \
  /opt/pantry-chef/.venv/bin/pantry-chef index /srv/cookbooks
```

Indexing is CPU-bound and parallel. On a shared box, cap it:

```bash
... /opt/pantry-chef/.venv/bin/pantry-chef index /srv/cookbooks --workers 2
```

Check the result:

```bash
sudo -u pantry PANTRY_CHEF_DB=/var/lib/pantry-chef/pantry-chef.db \
  /opt/pantry-chef/.venv/bin/pantry-chef stats
```

### A6. Run it as a service

```bash
sudo cp /opt/pantry-chef/deploy/pantry-chef.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pantry-chef
systemctl status pantry-chef
```

The unit binds to `127.0.0.1:8077`, runs with `ProtectSystem=strict`, writes to
nothing but `/var/lib/pantry-chef`, and mounts your books read-only.

> If your cookbooks live under `/home`, change `ProtectHome=yes` to
> `ProtectHome=read-only` in the unit, or systemd will hide them from the
> service and every book will fail to open.

Confirm it is up and *only* on loopback:

```bash
curl -s localhost:8077/api/stats | head -c 120
ss -tlnp | grep 8077          # should show 127.0.0.1:8077, never 0.0.0.0:8077
```

### A7. Put Caddy in front

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Generate a password hash, then edit the config:

```bash
caddy hash-password
sudo cp /opt/pantry-chef/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile      # set your hostname, username and the hash
sudo systemctl reload caddy
```

### A8. Verify

```bash
curl -sI https://recipes.example.com | head -1          # 401 without credentials
curl -sI -u niels:yourpassword https://recipes.example.com | head -1   # 200
```

Open **https://recipes.example.com** in a browser. It should ask for the
password, then show your library.

### A9. Day to day

```bash
journalctl -u pantry-chef -f                      # logs

# add books later
sudo cp newbook.epub /srv/cookbooks/
sudo -u pantry PANTRY_CHEF_DB=/var/lib/pantry-chef/pantry-chef.db \
  /opt/pantry-chef/.venv/bin/pantry-chef index /srv/cookbooks

# update
cd /opt/pantry-chef
sudo -u pantry git pull
sudo -u pantry .venv/bin/pip install ".[all]"
sudo systemctl restart pantry-chef

# back up the index
sudo -u pantry sqlite3 /var/lib/pantry-chef/pantry-chef.db \
  ".backup /backup/pantry-chef.db"
```

---

## Route B — Podman

Rootless, no daemon. Quadlet turns the container into a native systemd unit.

### B1. Install Podman

```bash
sudo apt install -y podman            # Debian/Ubuntu
sudo dnf install -y podman            # Fedora/RHEL
```

Let the service keep running when you are not logged in:

```bash
sudo loginctl enable-linger $USER
```

### B2. Put your cookbooks on the server

```bash
sudo mkdir -p /srv/cookbooks
rsync -av ~/Books/Cookbooks/ user@server:/tmp/cookbooks/     # from your laptop
sudo mv /tmp/cookbooks/* /srv/cookbooks/                     # on the server
sudo chmod -R a+r /srv/cookbooks
```

### B3. Build the image

```bash
git clone https://github.com/rnvegter/pantry-chef.git ~/pantry-chef
cd ~/pantry-chef
podman build -t pantry-chef -f Containerfile .
```

Takes well under a minute and produces a 257 MB image.

### B4. Build the index

```bash
podman run --rm \
  -v /srv/cookbooks:/books:ro,Z \
  -v pantry-chef-data:/data \
  pantry-chef pantry-chef index /books
```

`:Z` relabels for SELinux on Fedora and RHEL; it is harmless elsewhere. Drop the
`,Z` if your system does not use SELinux.

### B5. Run it as a service

```bash
mkdir -p ~/.config/containers/systemd
cp ~/pantry-chef/deploy/pantry-chef.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start pantry-chef
systemctl --user status pantry-chef
```

Quadlet generates a real systemd unit from that file, so it restarts on failure
and starts at boot. For a system-wide service put the file in
`/etc/containers/systemd/` and drop `--user`.

Confirm it is up and only on loopback:

```bash
curl -s localhost:8077/api/stats | head -c 120
ss -tlnp | grep 8077          # 127.0.0.1:8077 only
```

### B6. Put Caddy in front

```bash
sudo apt install -y caddy             # see A7 for the repository setup
caddy hash-password
sudo cp ~/pantry-chef/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile        # hostname, username, hash
sudo systemctl reload caddy
```

### B7. Verify

```bash
curl -sI https://recipes.example.com | head -1                          # 401
curl -sI -u niels:yourpassword https://recipes.example.com | head -1     # 200
```

### B8. Day to day

```bash
journalctl --user -u pantry-chef -f               # logs

# add books later
sudo cp newbook.epub /srv/cookbooks/
podman run --rm -v /srv/cookbooks:/books:ro,Z -v pantry-chef-data:/data \
  pantry-chef pantry-chef index /books

# update
cd ~/pantry-chef && git pull
podman build -t pantry-chef -f Containerfile .
systemctl --user restart pantry-chef

# back up the index
podman run --rm -v pantry-chef-data:/data -v /backup:/backup \
  pantry-chef cp /data/pantry-chef.db /backup/pantry-chef.db
```

---

## Route C — Docker

Uses `compose.yaml` from the repository, so one command builds and runs it.

### C1. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
```

To run Docker without `sudo` (log out and back in afterwards):

```bash
sudo usermod -aG docker $USER
```

> Adding yourself to the `docker` group is equivalent to root on that machine.
> On a shared box, prefer `sudo docker` or use Route B, which is rootless.

Compose v2 is included as a plugin — the command is `docker compose`, not
`docker-compose`.

### C2. Put your cookbooks on the server

```bash
sudo mkdir -p /srv/cookbooks
rsync -av ~/Books/Cookbooks/ user@server:/tmp/cookbooks/     # from your laptop
sudo mv /tmp/cookbooks/* /srv/cookbooks/                     # on the server
sudo chmod -R a+r /srv/cookbooks
```

### C3. Get the app and point it at your books

```bash
git clone https://github.com/rnvegter/pantry-chef.git ~/pantry-chef
cd ~/pantry-chef
echo "BOOKS=/srv/cookbooks" > .env
```

`compose.yaml` reads `BOOKS` for the read-only book mount. A `.env` file beside
it is picked up automatically, so you do not have to export it every time.

### C4. Start it

```bash
docker compose up -d
docker compose ps
```

The first run builds the image, which takes well under a minute.

### C5. Build the index

**Do this through Compose, not with a bare `docker run`.** Compose prefixes its
volumes with the project name, so the service reads
`pantry-chef_pantry-chef-data` while a bare `docker run -v pantry-chef-data:/data`
would write to a *different* volume and the app would report no database:

```bash
docker compose run --rm pantry-chef pantry-chef index /books
```

Check it:

```bash
docker compose run --rm pantry-chef pantry-chef stats
curl -s localhost:8077/api/stats | head -c 120
```

Confirm it is only on loopback — `compose.yaml` publishes to `127.0.0.1`
deliberately:

```bash
ss -tlnp | grep 8077          # 127.0.0.1:8077, never 0.0.0.0:8077
```

### C6. Survive a reboot

`compose.yaml` sets `restart: unless-stopped`, and the Docker daemon starts at
boot, so the container comes back on its own. Confirm the daemon is enabled:

```bash
sudo systemctl is-enabled docker
```

### C7. Put Caddy in front

```bash
sudo apt install -y caddy             # see A7 for the repository setup
caddy hash-password
sudo cp ~/pantry-chef/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile        # hostname, username, hash
sudo systemctl reload caddy
```

### C8. Verify

```bash
curl -sI https://recipes.example.com | head -1                          # 401
curl -sI -u niels:yourpassword https://recipes.example.com | head -1     # 200
```

### C9. Day to day

```bash
docker compose logs -f                            # logs

# add books later
sudo cp newbook.epub /srv/cookbooks/
docker compose run --rm pantry-chef pantry-chef index /books

# update
cd ~/pantry-chef && git pull
docker compose build && docker compose up -d

# back up the index
docker compose cp pantry-chef:/data/pantry-chef.db ./pantry-chef-backup.db

# stop, or remove entirely
docker compose stop
docker compose down -v            # -v also deletes the index; books are untouched
```

---

## Swapping in nginx

Any route can use nginx instead of Caddy. Replace the Caddy step with:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo htpasswd -c /etc/nginx/.pantry-chef.htpasswd niels
sudo cp deploy/nginx.conf /etc/nginx/sites-available/pantry-chef
sudo nano /etc/nginx/sites-available/pantry-chef      # set your hostname
sudo ln -s /etc/nginx/sites-available/pantry-chef /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d recipes.example.com
```

Unlike Caddy, nginx does not obtain certificates on its own — that is what the
`certbot` line is for.

---

## Locking it down

Pick at least one. They stack, and the first two are the ones worth doing.

| Approach | Effort | What it gives you |
|---|---|---|
| **Tailscale (or WireGuard)** | 10 minutes | The app is never on the public internet. Reachable from your own devices, invisible to everyone else. **The best answer here** — with no public exposure, the missing authentication stops mattering. |
| **Basic auth at the proxy** | 2 minutes | One shared password. Crude, but it is the difference between private and public, and it is in every config above. |
| Single-sign-on proxy | an afternoon | [Authelia](https://www.authelia.com/), [Authentik](https://goauthentik.io/) or [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/) in front, if you already run one |
| Firewall | 1 minute | Allow 80/443 only. Never expose 8077. |

With Tailscale you can skip certificates entirely and reach it at
`http://server:8077` over the tailnet, or use `tailscale serve` for HTTPS on
your tailnet domain.

---

## Keeping the books where they are

Recipe photos are read **out of the book files on demand** — the index stores a
reference, not a copy. Two consequences:

- The books must stay readable by the server for as long as you want photos.
- The path is recorded in the database. If it changes, one
  `pantry-chef index /new/path --force` rewrites it. That also reassigns recipe
  ids, so bookmarked `/recipe/<id>` links will not survive.

Mounting the books over NFS or SMB works, but indexing gets slower and a dropped
mount means missing photos until it returns.

The photo cache never needs backing up — delete it any time and it refills as
you browse.

---

## Two constraints worth knowing

### One worker, always

Run uvicorn with a **single worker**. Every route above does.

Indexing progress is held in memory in a per-process object. With more than one
worker, the browser polling for progress round-robins between them, so it sees a
job running on one request and no job on the next. Nothing corrupts — SQLite
serialises the writes — but the Library page becomes unusable.

One worker is plenty: queries run in single-digit to low-double-digit
milliseconds against a 50,000-recipe index, and the work is I/O-bound.

### Serve it at a domain root

Every link and fetch in the pages is absolute from `/` — `/static/app.css`,
`/api/search`, `/recipe/123`. So `recipes.example.com` works and
`example.com/pantry-chef/` does **not**: the pages would load and every asset
and API call would 404. Use a subdomain, or a dedicated host.

---

## What is verified here, and what is not

Being straight about it, since a wrong instruction in infrastructure costs you
an afternoon.

**Verified.** The container image, built on arm64 and used to index a real
6-book library to 586 recipes with no failures, serve every page and endpoint,
and extract recipe photographs live from a read-only book mount. The Compose
path specifically — `compose up`, `compose run … index`, then the running
service reading the result — which is how the project-prefixed volume trap in
step C5 was found rather than guessed. The application behind a real reverse
proxy, with the `Host` header rewritten and `X-Forwarded-*` set. The memory
figures, measured on the parser. The single-worker and domain-root constraints,
read out of the code.

**Not verified.** The `deploy/` files themselves — the systemd unit, the
Caddyfile, the nginx config — and every `apt`, `dnf`, `systemctl`, `certbot` and
`docker` command above. They were written on macOS, which has no systemd, Caddy,
nginx or Docker to run them against; Compose was exercised with
`podman-compose`, which reads the same file, rather than with Docker itself.
They follow each tool's documented usage and are conventional in shape, but your
first run is the real test. `systemd-analyze verify
/etc/systemd/system/pantry-chef.service`, `caddy validate`, `nginx -t` and
`docker compose config` will each check their own file before you commit to it.
