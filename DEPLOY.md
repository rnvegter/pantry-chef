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
> Every configuration in `deploy/` includes that protection. Do not strip it out.

There is also nothing to attack in the usual sense — no user accounts, no
uploads, no shell-outs, and the app only ever reads your books. The exposure is
your library, not your machine. That is still worth protecting.

---

## What the server needs

| | |
|---|---|
| **OS** | any Linux with systemd; also fine on a NAS that runs containers |
| **Python** | 3.11+ (not needed if you use the container route) |
| **RAM** | Very little. A parser process peaks at **~40 MB**, measured against books up to 312 MB — EPUBs are streamed out of the zip rather than loaded whole. Indexing runs one such process per worker, so even 8 workers is under 400 MB. A 1 GB VPS is comfortable. |
| **Disk** | your books, plus ~200 MB of index per 500 books, plus a photo cache that grows as you browse |
| **Books** | reachable from the server: local disk, or an NFS/SMB mount |

---

## Recommended tools

| Job | Use | Why |
|---|---|---|
| **Reverse proxy + HTTPS** | **[Caddy](https://caddyserver.com/)** | certificates are automatic; the whole config is six lines. This is the one to pick if you have no existing preference. |
| Reverse proxy, alternative | [nginx](https://nginx.org/) | pick it if you already run nginx and have certbot working |
| **Process supervision** | **systemd** | already on the box; restarts on failure and on boot |
| Containers | [Podman](https://podman.io/) + Quadlet | rootless, and Quadlet makes a container a native systemd unit |
| **Private access** | **[Tailscale](https://tailscale.com/)** | the best answer to the authentication problem: keep it off the public internet entirely |
| Backups | anything that copies one file | the database is a single SQLite file |

The ASGI server is **uvicorn**, which is already a dependency — you do not need
gunicorn, and you should not add worker processes (see
[One worker, always](#one-worker-always)).

---

## Route A — systemd + Caddy (recommended)

### 1. Create a user and lay out the directories

```bash
sudo useradd --system --home /opt/pantry-chef --shell /usr/sbin/nologin pantry
sudo mkdir -p /opt/pantry-chef /var/lib/pantry-chef
sudo chown pantry:pantry /var/lib/pantry-chef
```

### 2. Install the app

```bash
sudo -u pantry git clone https://github.com/rnvegter/pantry-chef.git /opt/pantry-chef
cd /opt/pantry-chef
sudo -u pantry python3 -m venv .venv
sudo -u pantry .venv/bin/pip install ".[all]"
```

### 3. Point it at your books and build the index

Assuming your cookbooks are at `/srv/cookbooks`:

```bash
sudo -u pantry PANTRY_CHEF_DB=/var/lib/pantry-chef/pantry-chef.db \
  /opt/pantry-chef/.venv/bin/pantry-chef index /srv/cookbooks
```

Indexing is CPU-bound and parallel. On a shared box, cap it so it does not take
every core for the couple of minutes it runs:

```bash
... pantry-chef index /srv/cookbooks --workers 2
```

### 4. Run it as a service

```bash
sudo cp deploy/pantry-chef.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pantry-chef
systemctl status pantry-chef
```

The unit binds to `127.0.0.1:8077` deliberately, so only the proxy can reach it.
It also runs with `ProtectSystem=strict`, writing to nothing but
`/var/lib/pantry-chef`, and mounts your books read-only.

> If your cookbooks live under `/home`, change `ProtectHome=yes` to
> `ProtectHome=read-only` in the unit, or systemd will hide them from the
> service and every book will fail to open.

### 5. Put Caddy in front

```bash
sudo caddy hash-password          # paste the hash into the Caddyfile
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Edit the hostname and the password hash first. Caddy obtains and renews the TLS
certificate on its own — there is no certbot step.

Open **https://recipes.example.com** and it will ask for the password.

---

## Route B — container + Quadlet

If you would rather not install Python on the server. Build the image first
(see the container section of [INSTALL.md](INSTALL.md#d-podman-or-docker) — note
that the image has not yet been built and tested).

```bash
cp deploy/pantry-chef.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start pantry-chef
```

Quadlet turns that file into a real systemd unit, so it restarts on failure and
starts at boot like anything else. For a system-wide service put it in
`/etc/containers/systemd/` instead.

Then put Caddy in front exactly as in Route A — the container publishes to
`127.0.0.1:8077`, same as the native service.

To keep the service running when you are not logged in:

```bash
sudo loginctl enable-linger $USER
```

---

## Route C — nginx

If nginx is already on the box:

```bash
sudo htpasswd -c /etc/nginx/.pantry-chef.htpasswd niels
sudo cp deploy/nginx.conf /etc/nginx/sites-available/pantry-chef
sudo ln -s /etc/nginx/sites-available/pantry-chef /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d recipes.example.com
```

The service side is identical to Route A.

---

## Locking it down

Pick at least one. They stack, and the first two are the ones worth doing.

| Approach | Effort | What it gives you |
|---|---|---|
| **Tailscale (or WireGuard)** | 10 minutes | The app is never on the public internet. Reachable from your own devices, invisible to everyone else. **The best answer here** — with no public exposure, the missing authentication stops mattering. |
| **Basic auth at the proxy** | 2 minutes | One shared password. Crude, but it is the difference between private and public, and it is already in the supplied configs. |
| Single-sign-on proxy | an afternoon | [Authelia](https://www.authelia.com/), [Authentik](https://goauthentik.io/) or [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/) in front, if you already run one |
| Firewall | 1 minute | Allow 80/443 only, and only from where you need. Never expose 8077. |

With Tailscale you can skip the certificate entirely and reach it at
`http://server:8077` over the tailnet — though `tailscale serve` will give you
HTTPS on your tailnet domain for free if you want it.

---

## Getting your books onto the server

The books have to be readable by the server, because **recipe photos are read
out of the book files on demand** — the index stores a reference, not a copy.
Move the books and the photos stop resolving.

- **Copy them** to `/srv/cookbooks`. Simplest and fastest.
- **Mount them** over NFS or SMB from a NAS. Works, but indexing gets slower and
  a dropped mount means missing photos until it comes back.

Either way, keep the path stable. The database records where each book lives, so
if the path changes you need one `pantry-chef index /new/path --force` to
rewrite it. That also reassigns recipe ids, so bookmarked `/recipe/<id>` links
will not survive.

---

## Day-to-day

**Watch it run**

```bash
journalctl -u pantry-chef -f
```

**Add books later** — drop them in the folder, then either use the **Library**
page in the browser, or:

```bash
sudo -u pantry PANTRY_CHEF_DB=/var/lib/pantry-chef/pantry-chef.db \
  /opt/pantry-chef/.venv/bin/pantry-chef index /srv/cookbooks
```

Unchanged books are skipped, so this is cheap to re-run.

**Update**

```bash
cd /opt/pantry-chef
sudo -u pantry git pull
sudo -u pantry .venv/bin/pip install ".[all]"
sudo systemctl restart pantry-chef
```

**Back up** — one file:

```bash
sudo -u pantry sqlite3 /var/lib/pantry-chef/pantry-chef.db ".backup /backup/pantry-chef.db"
```

Use `.backup` rather than copying the file, so you get a consistent snapshot
while the service is running. Strictly it is optional: your books are the source
of truth and the index can always be rebuilt from them. Restoring a backup is
just faster than re-reading 500 books.

The photo cache in `/var/lib/pantry-chef/image-cache/` never needs backing up —
delete it any time and it refills as you browse.

---

## Two constraints worth knowing

### One worker, always

Run uvicorn with a **single worker**. The unit and the container both do.

Indexing progress is held in memory in a per-process object. With more than one
worker, the browser polling for progress round-robins between them, so it sees a
job running on one request and no job on the next. Nothing corrupts — SQLite
serialises the writes — but the Library page becomes unusable.

One worker is plenty. Queries run in single-digit to low-double-digit
milliseconds against a 50,000-recipe index, and the work is I/O-bound.

### Serve it at a domain root

Every link and fetch in the pages is absolute from `/` — `/static/app.css`,
`/api/search`, `/recipe/123`. So `recipes.example.com` works, and
`example.com/pantry-chef/` does **not**: the pages would load and every asset
and API call would 404. Use a subdomain, or a dedicated host.

---

## What is verified here, and what is not

Being straight about it, since this is infrastructure and a wrong instruction
costs you an afternoon:

**Verified.** The memory figures above were measured on the parser, not
estimated. The application behind a reverse proxy — a proxy was put in front
of it with the `Host` header rewritten and `X-Forwarded-*` set, and the pages,
stylesheet, favicon, search API, recipe API and on-demand photo endpoint were
all exercised through it. The single-worker constraint and the domain-root
constraint were both confirmed by inspection of the code, not assumed.

**Not verified.** The `deploy/` files themselves. They were written on macOS,
where there is no systemd, no Caddy and no nginx to run them against. They
follow the documented syntax for each tool and are conventional in shape, but
the first `systemctl start` is your test, not mine. `systemd-analyze verify
/etc/systemd/system/pantry-chef.service`, `caddy validate` and `nginx -t` will
each check their own file before you commit to it.
