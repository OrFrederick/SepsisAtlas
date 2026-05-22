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
- The VPS has no source checkout of the repo. Only the two compose YAML files, `.env`, the `data/` bind-mount directory, and the private caddy overrides live on the box.

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
   │  ┌──────────────────────┐  ┌────────────────────────────┐│
   │  │ frontend             │  │ backend                    ││
   │  │ caddy:2 +            │  │ FastAPI                    ││
   │  │   dist/  (HTML/JS)   │  │ /query /viewer/* /health   ││
   │  │   pdfs/  (baked in)  │  │ /papers/*/pdf /phenotypes  ││
   │  │   data/  (baked in)  │  │ /static/*                  ││
   │  │   API → backend:8000 │◄─┤ volumes: ./data db.sqlite  ││
   │  │ :80 :443 :443/udp    │  │                            ││
   │  │ /etc/caddy/conf.d/   │  │                            ││
   │  │   ← host mount       │  │                            ││
   │  └──────────────────────┘  └────────────────────────────┘│
   │  watchtower (polls ghcr, restarts both via label)        │
   └──────────────────────────────────────────────────────────┘
```

## Key design choices

### Data is committed to the repo; CI bakes it into the frontend image

This is the load-bearing design call. The Astro pages use `getStaticPaths()` to emit one HTML page per paper, and that step has to read `web/public/data/{papers,rows}.json` *at build time* — there's no way to defer it to runtime without dropping SSG, which PR #41 explicitly preserves.

Implication: the JSON exports have to be present in the CI build context. The cleanest way to do that is to commit them to the repo, matching the existing convention for PDFs (which are already git-tracked in `data/papers/raw/`, despite stale wording in CLAUDE.md). The model becomes uniform: paper data and paper PDFs live in the repo, the user commits new ones after each pipeline run, CI rebuilds with fresh data.

Concretely:

- `data/papers/raw/*.pdf` stays committed (already the case).
- `web/public/data/{rows,papers,manifest}.json` starts being committed. One-time bootstrap step in the cutover: scp the current contents off the VPS, `git add`, commit.
- The frontend Dockerfile's build stage runs the existing `bun run build`, which already calls `seed-data.mjs` (stubs only if missing — won't overwrite committed real data) and `vendor-pdfjs.mjs`, then `astro build`. Plus a `rsync data/papers/raw/ web/public/pdfs/` step before `bun run build`, replicating what `deploy-main.sh` does on the VPS today.

After this change, the frontend image is fully self-contained: it ships HTML, JS, CSS, PDFs, and JSON. The backend stops serving any static assets. No new FastAPI routes are needed for this work.

Trade-off: every pipeline run that produces new JSON exports now requires a commit + push to ship the data to prod. Same model PDFs already follow. The user is OK with this.

### Combined caddy + Astro image

One image, one compose service. Multi-stage Dockerfile: stage 1 (`oven/bun:1.3.13-alpine`) runs `bun install --frozen-lockfile` + `bun run build`. Stage 2 (`caddy:2-alpine`) copies the resulting `dist/` to `/srv` and bakes in `deploy/Caddyfile` at `/etc/caddy/Caddyfile`.

Rejected alternative: two separate images (caddy + static sidecar). More moving parts, no benefit at this scale.

The Caddyfile changes:

- `reverse_proxy 127.0.0.1:8000` → `reverse_proxy backend:8000` (compose service DNS).
- `@api` matcher is unchanged. PDFs and JSON ship inside the frontend image, served by caddy's `file_server`. The matcher only catches backend routes (`/query`, `/viewer/*`, `/papers/*/pdf`, `/health`, `/phenotypes`, `/static/*`, etc.) and reverse-proxies them.
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

### VPS filesystem layout

After cutover, `/opt/sepsisatlas/` contains exactly:

```
/opt/sepsisatlas/
├── docker-compose.yml         # fetched from raw.githubusercontent.com
├── docker-compose.prod.yml    # fetched from raw.githubusercontent.com
├── update-compose.sh          # fetched from raw.githubusercontent.com
├── .env                       # user-managed, gitignored, never overwritten
├── db.sqlite                  # bind-mounted into backend
├── data/                      # bind-mounted into backend (PDFs, exports)
├── static/                    # bind-mounted into backend (plots)
├── runs/                      # bind-mounted into backend (run artifacts)
└── logs/                      # bind-mounted into backend (logs)
```

The bind-mount targets must match `docker-compose.prod.yml`'s `volumes:` list under the backend service exactly. If a future change adds a volume, that volume's host path must also exist in `/opt/sepsisatlas/` before `up -d`.

No git checkout. The old `/opt/sepsisatlas/main/` clone is removed during cutover.

Updates to either compose file are pulled from `raw.githubusercontent.com` via a small wrapper script (`update-compose.sh`) committed to the repo. The script is intentionally tiny — it's the *only* on-VPS escape hatch left for changing infrastructure, and a manual `curl` is fine for how rarely the compose files change.

The script's content (committed to the repo, fetched the same way as the compose files on first install):

```sh
#!/usr/bin/env bash
# Refresh the compose files on the VPS from main and restart the stack.
# Run from /opt/sepsisatlas as the deploy user (must be in the docker group).
set -euo pipefail

RAW="https://raw.githubusercontent.com/OrFrederick/SepsisAtlas/main"
PROJECT="atlas-main"

cd /opt/sepsisatlas
curl -fsSL "$RAW/docker-compose.yml"      -o docker-compose.yml
curl -fsSL "$RAW/docker-compose.prod.yml" -o docker-compose.prod.yml
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" up -d --remove-orphans
```

### Deletions

- `deploy/deploy-main.sh` — removed.
- `/var/www/atlas-main` on the VPS — removed during cutover.
- `/opt/sepsisatlas/main/` on the VPS — removed during cutover (the clone is no longer needed).

## Data flow

1. Browser requests `https://atlas.efferon.com/papers/Smith2020`.
2. Caddy container terminates TLS. URL doesn't match the `@api` matcher, falls through to `try_files {path} {path}/index.html /index.html`, serves `dist/papers/Smith2020/index.html` from `/srv` (pre-rendered at CI build time from committed `papers.json` + `rows.json`).
3. JS on that page requests `/pdfs/Smith2020.pdf`. URL doesn't match `@api`, served as a static file from `/srv/pdfs/Smith2020.pdf` (baked into the image at build time from `data/papers/raw/`).
4. JS makes a backend call, e.g. `POST /query`. URL matches `@api`, caddy reverse-proxies to `backend:8000`.

## Cutover plan (one-time, on prod VPS)

Order matters; this is the runbook the implementation plan will follow.

0. **Before merging:** scp the current contents of `/opt/sepsisatlas/main/web/public/data/{rows,papers,manifest}.json` off the VPS, place them at `web/public/data/` in the working branch, `git add web/public/data/*.json`, commit. This is the one-time data bootstrap. Without it, the first CI build will emit a frontend image with stub-empty data.
1. CI run completes on `main`, both `:main` tags exist on ghcr (`backend` and `frontend`).
2. On the VPS, as the `deploy` user:
   - `sudo systemctl disable --now caddy`
   - `sudo apt-get purge -y caddy`
   - `sudo mkdir -p /etc/sepsisatlas/caddy-conf.d && sudo mv /etc/caddy/conf.d/* /etc/sepsisatlas/caddy-conf.d/ 2>/dev/null || true`
   - Move the bind-mounted directories and `.env` out of the soon-to-be-deleted clone into the new layout. The set must match the volumes in `docker-compose.prod.yml` (`./data`, `./db.sqlite`, `./static`, `./runs`, `./logs`):
     ```sh
     sudo mkdir -p /opt/sepsisatlas
     for d in data static runs logs; do
       [ -d "/opt/sepsisatlas/main/$d" ] && sudo mv "/opt/sepsisatlas/main/$d" "/opt/sepsisatlas/$d"
     done
     sudo mv /opt/sepsisatlas/main/db.sqlite /opt/sepsisatlas/db.sqlite
     sudo mv /opt/sepsisatlas/main/.env     /opt/sepsisatlas/.env
     sudo chown -R deploy:deploy /opt/sepsisatlas
     ```
   - Fetch the compose files and the update wrapper from raw.githubusercontent.com:
     ```sh
     RAW="https://raw.githubusercontent.com/OrFrederick/SepsisAtlas/main"
     curl -fsSL "$RAW/docker-compose.yml"      -o /opt/sepsisatlas/docker-compose.yml
     curl -fsSL "$RAW/docker-compose.prod.yml" -o /opt/sepsisatlas/docker-compose.prod.yml
     curl -fsSL "$RAW/update-compose.sh"       -o /opt/sepsisatlas/update-compose.sh
     chmod +x /opt/sepsisatlas/update-compose.sh
     ```
   - Bring up the new stack:
     ```sh
     cd /opt/sepsisatlas
     docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main pull
     docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main up -d --remove-orphans
     ```
3. Watch `docker logs -f atlas-frontend` for the LE cert issuance (~30s).
4. Verify:
   - `curl -fsS https://atlas.efferon.com/health` → 200.
   - Load `https://atlas.efferon.com/papers/<any-known-stem>` in a browser; PDF viewer renders.
   - `curl -fsS https://atlas.efferon.com/data/manifest.json` → 200, non-empty.
5. After 24h of clean operation: `sudo rm -rf /var/www/atlas-main /opt/sepsisatlas/main`.

Rollback: `docker compose down`, re-clone the repo into `/opt/sepsisatlas/main` (the data/db.sqlite/.env will need to move back), re-install `caddy` (`apt-get install -y caddy`), restore the old `Caddyfile`, `systemctl enable --now caddy`, re-run the old `deploy-main.sh` (kept in git history). Backend is unaffected throughout.

## Risks

- **Frontend builds out-of-date data into the image.** Now that `web/public/data/*.json` is committed, "shipping fresh data to prod" requires a commit + push, not just a pipeline run. If a teammate forgets, prod serves stale data until the next CI build. Mitigation: extraction pipeline writes to `web/public/data/`; the project's existing "commit your changes" muscle memory should catch this. A future improvement could add an `extract.py`-side `git status` warning when uncommitted JSON exports exist.
- **`/etc/caddy/conf.d/` content on the VPS.** If basic-auth or IP-allowlist directives are currently in use, the path move (`/etc/caddy/conf.d/` → `/etc/sepsisatlas/caddy-conf.d/`) must preserve them. The cutover step explicitly moves the directory; verify with `ls /etc/sepsisatlas/caddy-conf.d/` before bringing the container up.
- **Watchtower pulling a broken frontend image.** Same risk as the backend has today. Mitigation: same as backend — push to a feature branch, manually pull on the VPS before merging if uncertain.
- **Image size growth.** Baking 30+ PDFs (~50-200 MB total) plus JSON into the frontend image is a one-time bloat. Acceptable: image build runs in CI on cached layers, `docker pull` only fetches the PDF layer when it changes (it'll only invalidate when a new PDF lands). If image size becomes a real problem later (say > 1 GB), the follow-up is to split PDFs into their own image or a shared volume.

## Verification (post-cutover)

- `/health` returns 200.
- A PDF viewer page renders a PDF (no 404 on `/pdfs/<stem>.pdf`).
- `docker logs atlas-frontend` shows TLS handshake from a real browser.
- Push a no-op `web/README.md` edit to `main`; observe `build-frontend` runs in CI, no `deploy` job exists, and within 5–10 min `docker images` on the VPS shows the new frontend image digest pulled by Watchtower.
- Pushed `:${{ github.sha }}` tag exists on ghcr for both images.

## Open questions for implementation

These are resolved at implementation time, not in this spec:

- Whether the `bun.lock` checked into `web/` is up to date relative to `package.json` (it has to be, for `--frozen-lockfile` to succeed in CI).
- Whether the extraction pipeline already writes to `web/public/data/` (so committing is a no-op for the dev workflow), or writes elsewhere and we need to point it at `web/public/data/`.
