# Containerize the frontend, remove SSH-from-CI deploys

Date: 2026-05-22
Status: Design (approved verbally, pending written review)

## Problem

Today the deploy pipeline has two delivery mechanisms:

- **Backend** is a container. CI builds and pushes `ghcr.io/orfrederick/sepsis-atlas-backend:main`. Watchtower on the VPS polls ghcr every 5 minutes and restarts the backend when a new tag lands. This works well — push to `main`, prod updates on its own.
- **Frontend + Caddy** are not containers. A GitHub Actions `deploy` job SSHes into the VPS with a deploy-user SSH key (`DEPLOY_SSH_HOST/USER/KEY` secrets), runs `deploy/deploy-main.sh`, which `git fetch`es on the box, runs `bun run build`, rsyncs the result to `/var/www/atlas-main`, validates and reloads the host `caddy` systemd unit.

Two problems with the second mechanism:

1. CI needs a long-lived SSH key into prod. That key has full shell access as the `deploy` user (which is in the `docker` group). It's a credential we'd rather not have in GitHub secrets.
2. The two delivery models are asymmetric — backend self-updates, frontend requires CI to push a button. Different mental model, easy to forget.

## Goal

Make the entire stack ship from ghcr the same way the backend does today. After this work:

- Pushing to `main` triggers CI image builds only — no SSH, no remote shell execution.
- Watchtower on the VPS pulls and restarts both containers (backend + frontend) on its existing 5-minute poll.
- The VPS has no host services beyond docker itself (no host `caddy`, no `/var/www/atlas-main` directory).

## Non-goals

- Multi-host or HA. Single-VPS deploy remains.
- Zero-downtime cutover. ~30–60s of TLS unavailability during the one-time host-caddy → container-caddy switch is acceptable.
- Reworking how the extraction pipeline writes data exports. The exporter still writes to the VPS filesystem; only the way those files are served changes.
- Containerizing the extraction pipeline itself. Out of scope.

## Architecture (target state)

```
                push to main
                     │
                     ▼
   ┌──────────── GitHub Actions ────────────┐
   │  build-backend  ──► ghcr.io/.../backend │
   │  build-frontend ──► ghcr.io/.../frontend│
   └─────────────────────────────────────────┘
                     │
                     ▼ (Watchtower polls ghcr every 5 min)
   ┌──────────────── VPS (atlas.efferon.com) ─────────────────┐
   │  docker compose -f docker-compose.yml -f .prod.yml       │
   │  ┌───────────────────┐    ┌────────────────────────────┐ │
   │  │ frontend          │    │ backend                    │ │
   │  │ caddy:2 + dist/   │───►│ FastAPI                    │ │
   │  │ :80 :443 :443/udp │    │ /query /viewer /pdfs /data │ │
   │  │ /etc/caddy/conf.d │    │ /papers/*/pdf etc.         │ │
   │  │   ← host mount    │    │ volumes: ./data db.sqlite  │ │
   │  └───────────────────┘    └────────────────────────────┘ │
   │  watchtower (polls ghcr, restarts both via label)        │
   └──────────────────────────────────────────────────────────┘
```

## Key design choices

### Backend serves all dynamic data

The frontend baked static files into its build today: `data/papers/raw/*.pdf` rsynced into `web/public/pdfs/`, and `web/public/data/{rows,papers,manifest}.json` written by the extraction exporter. Those files live only on the VPS (gitignored), so a CI build can't include them.

The clean answer: the backend already owns the `data/` volume; have it serve those files over HTTP. The frontend image then ships *only* static HTML/JS/CSS and is fully portable.

Two new FastAPI routes:

- `GET /pdfs/{stem}.pdf` — streams `data/papers/raw/{stem}.pdf`. The frontend viewer already references this URL today (it was served by host caddy from the static dir); the URL surface doesn't change, only what's behind it.
- `GET /data/{name}.json` — whitelist of `rows`, `papers`, `manifest`. Streams from wherever the exporter writes today (likely `data/web-exports/` or similar — confirm path during implementation; do not invent a path). 404 if missing.

Both routes use `fastapi.responses.FileResponse`, no in-memory load. Stem validation: reject anything that isn't `[A-Za-z0-9_-]+` to prevent path traversal.

### Combined caddy + Astro image

One image, one compose service. Multi-stage Dockerfile: stage 1 (`oven/bun:1.3.13-alpine`) runs `bun install --frozen-lockfile` + `bun run build`. Stage 2 (`caddy:2-alpine`) copies the resulting `dist/` to `/srv` and bakes in `deploy/Caddyfile` at `/etc/caddy/Caddyfile`.

Rejected alternative: two separate images (caddy + static sidecar). More moving parts, no benefit at this scale.

The Caddyfile changes:

- `reverse_proxy 127.0.0.1:8000` → `reverse_proxy backend:8000` (compose service DNS).
- Add `path /pdfs/*` and `path /data/*` to the `@api` matcher so those routes also reverse-proxy to backend.
- Drop the `log { output file ... }` directive. Caddy logs to stdout; `docker logs atlas-frontend` (or `compose logs`) captures it; docker's `json-file` driver handles rotation.
- `root * /var/www/atlas-main` → `root * /srv` (the COPY target in the image).
- Keep `import /etc/caddy/conf.d/*.caddy`. The container bind-mounts `/etc/sepsisatlas/caddy-conf.d/` from the host as `:ro`. Empty by default; this is where private basic-auth / IP-allowlist directives go if needed. The Dockerfile pre-creates `/etc/caddy/conf.d/` so the import doesn't fail when the host dir is empty.

### Compose service shape

`docker-compose.prod.yml` gains:

```yaml
services:
  frontend:
    image: ghcr.io/orfrederick/sepsis-atlas-frontend:main
    pull_policy: missing
    labels:
      com.centurylinklabs.watchtower.enable: "true"
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - caddy_data:/data
      - caddy_config:/config
      - /etc/sepsisatlas/caddy-conf.d:/etc/caddy/conf.d:ro
    depends_on:
      backend:
        condition: service_healthy
    restart: unless-stopped
    networks: [sepsis]

volumes:
  caddy_data:
  caddy_config:
```

`pull_policy: missing` matches the backend's existing pattern: a transient ghcr outage during deploy doesn't tear down the running container. Ongoing image refreshes are owned by Watchtower, which already has the right label filter.

The backend service in `docker-compose.prod.yml` no longer needs `ports: 127.0.0.1:8000:8000` exposed to the host — caddy talks to it over the compose network. Drop that port mapping; it's the only thing currently making the backend reachable from outside compose, and nothing else on the VPS needs it after this change.

### TLS

Container caddy re-issues a fresh Let's Encrypt cert on first request after the cutover. ~30–60s of TLS unavailability during the swap. LE rate limits (50 certs/week/domain) leave plenty of headroom.

Cert + ACME state persist across container restarts via the `caddy_data` named volume. Rejected alternative: copying `/var/lib/caddy/.local/share/caddy/` from host to the named volume. Possible but fiddly (caddy's internal path layout has to be preserved exactly); not worth the saved minute of downtime for this site.

### CI workflow

`.github/workflows/deploy.yml` changes:

- `changes` job: add a `frontend` output, tracking `web/**`, `docker/Dockerfile.frontend`, `deploy/Caddyfile`.
- Add `build-frontend` job mirroring `build-backend`: checkout, buildx, login, build+push with tags `:main` and `:${{ github.sha }}`, gha cache. Builds whenever the `frontend` filter fires or `workflow_dispatch` is used.
- Delete the `deploy` job entirely. No replacement.
- Remove the file header bullet about "ssh to VPS and run deploy-main.sh".

Secrets `DEPLOY_SSH_HOST`, `DEPLOY_SSH_USER`, `DEPLOY_SSH_KEY` become unused. The spec recommends removing them from the GitHub repo settings after the cutover succeeds, but the implementation does not gate on this.

### bootstrap.sh

`deploy/bootstrap.sh` stays, with the caddy-install step removed. Specifically:

- The function that runs `curl -1sLf 'https://dl.cloudsmith.io/...' | gpg --dearmor ...` + `apt install caddy` is deleted.
- The systemd `enable --now caddy` line is deleted.
- The function that copies `deploy/Caddyfile` to `/etc/caddy/Caddyfile` is deleted.
- ufw rules opening tcp/80 and tcp/443 stay (the container needs them open on the host).
- sshd hardening, fail2ban, deploy-user creation, docker install — all unchanged.

The `bootstrap.sh` header comment is updated to reflect the new responsibility set.

### Deletions

- `deploy/deploy-main.sh` — removed.
- `/var/www/atlas-main` on the VPS — removed during cutover, no longer referenced.

## Data flow

1. Browser requests `https://atlas.efferon.com/papers/Smith2020`.
2. Caddy container terminates TLS. URL doesn't match the `@api` matcher, falls through to `try_files {path} {path}/index.html /index.html`, serves `dist/papers/Smith2020/index.html` from `/srv`.
3. JS on that page fetches `/pdfs/Smith2020.pdf` and `/data/manifest.json`.
4. Both URLs match `@api`, caddy reverse-proxies to `backend:8000`. Backend streams the file from its `./data` volume.

## Cutover plan (one-time, on prod VPS)

Order matters; this is the runbook the implementation plan will follow.

1. CI run completes on `main`, both `:main` tags exist on ghcr (`backend` and `frontend`).
2. On the VPS, as the `deploy` user:
   - `sudo systemctl disable --now caddy`
   - `sudo apt-get purge -y caddy`
   - `sudo mkdir -p /etc/sepsisatlas/caddy-conf.d && sudo mv /etc/caddy/conf.d/* /etc/sepsisatlas/caddy-conf.d/ 2>/dev/null || true`
   - `cd /opt/sepsisatlas/main && git pull origin main` (picks up the new compose + Dockerfile + workflow files)
   - `docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main pull`
   - `docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main up -d --remove-orphans`
3. Watch `docker logs -f atlas-frontend` for the LE cert issuance (~30s).
4. Verify:
   - `curl -fsS https://atlas.efferon.com/health` → 200.
   - Load `https://atlas.efferon.com/papers/<any-known-stem>` in a browser; PDF viewer renders.
   - `curl -fsS https://atlas.efferon.com/data/manifest.json` → 200, non-empty.
5. After 24h of clean operation: `sudo rm -rf /var/www/atlas-main`.

Rollback: `docker compose down`, re-install `caddy` (`apt-get install -y caddy`), put the old `Caddyfile` back, `systemctl enable --now caddy`, re-run the old `deploy-main.sh` (kept in git history). Backend is unaffected throughout.

## Risks

- **Astro pages that bake JSON data at build time, not runtime.** If any page imports `papers.json` or similar at SSG time, the published image will contain stub-empty data (the CI build only has `seed-data.mjs`'s empty placeholders). Mitigation: implementation step explicitly inspects the built `dist/` for non-stub data, and grep'd Astro source for build-time imports of the JSON files. If any are found, those pages must move to client-side fetch before this change ships.
- **`/etc/caddy/conf.d/` content on the VPS.** If basic-auth or IP-allowlist directives are currently in use, the path move (`/etc/caddy/conf.d/` → `/etc/sepsisatlas/caddy-conf.d/`) must preserve them. The cutover step explicitly moves the directory; verify with `ls /etc/sepsisatlas/caddy-conf.d/` before bringing the container up.
- **Watchtower pulling a broken frontend image.** Same risk as the backend has today. Mitigation: same as backend — push to a feature branch, manually pull on the VPS before merging if uncertain.
- **PDF served-by-backend latency.** The backend will now stream PDFs through Python. PDFs are static files (up to a few MB each). `FileResponse` uses `sendfile(2)` where supported, so this should be effectively as fast as caddy serving them directly. If latency turns out to be a problem in practice, a follow-up can move `/pdfs/*` to a caddy `file_server` from a shared volume.

## Verification (post-cutover)

- `/health` returns 200.
- A PDF viewer page renders a PDF (no 404 on `/pdfs/<stem>.pdf`).
- `docker logs atlas-frontend` shows TLS handshake from a real browser.
- Push a no-op `web/README.md` edit to `main`; observe `build-frontend` runs in CI, no `deploy` job exists, and within 5–10 min `docker images` on the VPS shows the new frontend image digest pulled by Watchtower.
- Pushed `:${{ github.sha }}` tag exists on ghcr for both images.

## Open questions for implementation

These are resolved at implementation time, not in this spec:

- Exact filesystem path the extraction exporter writes the `rows.json`/`papers.json`/`manifest.json` to today. The new backend route reads from that same path; do not invent one.
- Whether any Astro page imports those JSON files at build time (see Risks).
- Whether the `bun.lock` checked into `web/` is up to date relative to `package.json` (it has to be, for `--frozen-lockfile` to succeed in CI).
