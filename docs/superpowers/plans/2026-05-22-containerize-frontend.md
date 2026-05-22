# Containerize Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the SSH-from-CI deploy with a second ghcr image (frontend = caddy + Astro `dist` + PDFs + JSON, all baked in), wired into compose alongside the existing backend so Watchtower handles updates.

**Architecture:** One new image `ghcr.io/orfrederick/sepsis-atlas-frontend` built by a new GitHub Actions job. Frontend container terminates TLS, serves static assets from `/srv`, reverse-proxies API routes to the backend over the compose network. Host caddy is removed; the VPS keeps only docker, the two compose YAMLs, `.env`, and the bind-mounted state directories. Spec: `docs/superpowers/specs/2026-05-22-containerize-frontend-design.md`.

**Tech Stack:** Docker multi-stage build (`oven/bun:1.3.13-alpine` → `caddy:2-alpine`), GitHub Actions (`docker/build-push-action@v5`), docker-compose v2, Caddy v2.

---

## Pre-flight (user action, before any code change)

**You — the user — must do this once before the first CI build runs to completion.** The plan can be developed without it, but the first prod cutover will be broken until it's done.

- [ ] **Step P1: Pull JSON exports off the VPS and commit them.**

```bash
# On your laptop, in the deploy-changes worktree:
scp efferon-deploy:/opt/sepsisatlas/main/web/public/data/rows.json    web/public/data/rows.json
scp efferon-deploy:/opt/sepsisatlas/main/web/public/data/papers.json  web/public/data/papers.json
scp efferon-deploy:/opt/sepsisatlas/main/web/public/data/manifest.json web/public/data/manifest.json
git add web/public/data/*.json
git commit -m "data: bootstrap web exports for CI builds

One-time commit of rows.json, papers.json, manifest.json out of the prod
VPS so CI has them when building the frontend image. From now on, the
extraction pipeline writes here and these files get committed alongside
new PDFs."
```

If the files don't exist on the VPS (because the exporter never ran), skip this step — CI will build the frontend with `seed-data.mjs`'s empty stubs, the table views will be empty, but the cutover will still succeed and the next pipeline run + commit will populate data.

---

## Task 1: Allow Caddyfile and raw PDFs into the build context

**Files:**
- Modify: `.dockerignore`

The current `.dockerignore` excludes `data/` and `deploy/` to keep the backend build context small. The frontend Dockerfile needs `deploy/Caddyfile` and the PDFs under `data/papers/raw/`. Whitelist them with `!`.

- [ ] **Step 1.1: Edit `.dockerignore`.**

Open `.dockerignore` and apply the following changes:

Replace the line:

```
data
```

with:

```
data
!data/papers/raw
```

Replace the line:

```
deploy
```

with:

```
deploy
!deploy/Caddyfile
```

The backend image is unaffected — its Dockerfile only `COPY`s `pyproject.toml` and `src/`, so the whitelisted paths don't end up in the backend image. They're only in the build context.

- [ ] **Step 1.2: Verify the build context is correct for the backend.**

Run:

```bash
docker build -f docker/Dockerfile.backend -t sepsis-atlas-backend:test .
```

Expected: build succeeds (cached layers from prior builds may be reused). If this fails, the `.dockerignore` change broke something — revert and investigate before continuing.

- [ ] **Step 1.3: Commit.**

```bash
git add .dockerignore
git commit -m "chore(docker): whitelist Caddyfile + raw PDFs in build context

The new frontend Dockerfile needs deploy/Caddyfile and data/papers/raw/.
Excluded by default to keep the backend build context tight; whitelist
them explicitly with ! so they're available to the frontend stage."
```

---

## Task 2: Write the frontend Dockerfile

**Files:**
- Create: `docker/Dockerfile.frontend`

Multi-stage build: Bun stage installs deps and runs `astro build` (which already calls `seed-data.mjs` and `vendor-pdfjs.mjs`); rsync of PDFs into `web/public/pdfs/` happens between deps install and build to mirror what `deploy-main.sh` does today. Final stage is `caddy:2-alpine` with `dist/` at `/srv` and the Caddyfile baked in.

- [ ] **Step 2.1: Create `docker/Dockerfile.frontend`.**

```dockerfile
# syntax=docker/dockerfile:1.7

# ---- Stage 1: build the Astro site with PDFs included ----
FROM oven/bun:1.3.13-alpine AS build

WORKDIR /app

# Install rsync (Alpine doesn't ship it by default; we use it to mirror the
# PDF set into web/public/pdfs/ exactly the way deploy-main.sh did).
RUN apk add --no-cache rsync

# Cache deps on package.json + bun.lock only.
COPY web/package.json web/bun.lock ./web/
RUN cd web && bun install --frozen-lockfile

# Web source.
COPY web ./web

# Raw PDFs go next to the source so the build can rsync them in. Doing the
# rsync inside the image (rather than from the build context directly into
# web/public/pdfs/) keeps the file layout obvious in case the build is
# debugged later.
COPY data/papers/raw ./data/papers/raw
RUN mkdir -p web/public/pdfs && \
    rsync -a --delete --include='*.pdf' --exclude='*' \
      data/papers/raw/ web/public/pdfs/

# Build the static site. PUBLIC_BACKEND_URL="" mirrors deploy-main.sh's
# call — same-origin fetches via caddy.
RUN cd web && PUBLIC_BACKEND_URL="" bun run build

# ---- Stage 2: caddy serving the built dist ----
FROM caddy:2-alpine

# Pre-create the empty conf.d dir so `import /etc/caddy/conf.d/*.caddy` in
# the Caddyfile doesn't fail when the host bind-mount is empty.
RUN mkdir -p /etc/caddy/conf.d

COPY --from=build /app/web/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile

EXPOSE 80 443 443/udp
```

- [ ] **Step 2.2: Build the image locally and verify its contents.**

```bash
docker build -f docker/Dockerfile.frontend -t sepsis-atlas-frontend:test . 2>&1 | tail -20
```

Expected: build completes without error, last line `Successfully tagged sepsis-atlas-frontend:test` (or BuildKit-equivalent).

If it fails on `bun install --frozen-lockfile`, the `web/bun.lock` doesn't match `web/package.json`. Resolve by running `cd web && bun install` locally, committing the lockfile, retry.

If it fails on the PDF rsync because `data/papers/raw/` is empty, the `.dockerignore` whitelist from Task 1 didn't land — go re-verify.

- [ ] **Step 2.3: Smoke-check image contents.**

```bash
docker run --rm sepsis-atlas-frontend:test sh -c '
  echo "=== /srv ==="
  ls /srv | head -10
  echo "=== /srv/pdfs (count) ==="
  ls /srv/pdfs | wc -l
  echo "=== /srv/data ==="
  ls /srv/data
  echo "=== /etc/caddy/Caddyfile (head) ==="
  head -5 /etc/caddy/Caddyfile
  echo "=== caddy validate ==="
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1 | tail -5
'
```

Expected output (approximate):

```
=== /srv ===
index.html
papers
viewer
rank
... (Astro output)
=== /srv/pdfs (count) ===
30
=== /srv/data ===
manifest.json
papers.json
rows.json
=== /etc/caddy/Caddyfile (head) ===
# Sepsis Atlas — atlas.efferon.com
...
=== caddy validate ===
Valid configuration
```

If `/srv/pdfs` count is 0, the rsync step inside the Dockerfile didn't pick up PDFs — re-check `.dockerignore` and the `COPY data/papers/raw` line. If `caddy validate` fails, the Caddyfile reverse_proxy target hasn't been updated yet — that happens in Task 3; it's OK to defer this check till after Task 3.

- [ ] **Step 2.4: Commit.**

```bash
git add docker/Dockerfile.frontend
git commit -m "feat(docker): add Dockerfile.frontend

Multi-stage build: bun installs deps, rsyncs data/papers/raw into
web/public/pdfs/, runs astro build. Final stage is caddy:2-alpine with
the resulting dist/ baked in at /srv and the Caddyfile at
/etc/caddy/Caddyfile. Ready for the build-frontend job to push it to
ghcr."
```

---

## Task 3: Update the Caddyfile for in-container routing

**Files:**
- Modify: `deploy/Caddyfile`

Changes:

- `root * /var/www/atlas-main` → `root * /srv`.
- `reverse_proxy 127.0.0.1:8000` → `reverse_proxy backend:8000` (compose service DNS).
- Remove the `log { output file ... }` block — caddy logs to stdout, captured by docker.

The `@api` matcher is unchanged. PDFs and JSON ship inside the image; they fall through to `file_server` like the rest of `/srv`.

- [ ] **Step 3.1: Edit `deploy/Caddyfile`.**

Replace the line:

```
	root * /var/www/atlas-main
```

with:

```
	root * /srv
```

Replace the line:

```
		reverse_proxy 127.0.0.1:8000
```

with:

```
		reverse_proxy backend:8000
```

Delete these lines (the entire `log { ... }` block, including the surrounding blank line):

```

	log {
		output file /var/log/caddy/atlas.efferon.com.access.log
	}
```

Also update the comment at the top to reflect the new context — replace:

```
# Sepsis Atlas — atlas.efferon.com
#
# Path matchers below mirror src/api/main.py exactly. They are tight on
# purpose — trailing `*` was hijacking sibling Astro pages (/papers/index
# was being routed to the backend instead of being served as a static SPA).
# Any new FastAPI route must be added here as well.
```

with:

```
# Sepsis Atlas — atlas.efferon.com
#
# This Caddyfile is baked into the frontend container image at
# /etc/caddy/Caddyfile. The static site lives at /srv (Astro dist + PDFs +
# data JSON, all built into the image). API routes reverse-proxy to the
# `backend` compose service over the internal network.
#
# Path matchers below mirror src/api/main.py exactly. They are tight on
# purpose — trailing `*` was hijacking sibling Astro pages (/papers/index
# was being routed to the backend instead of being served as a static SPA).
# Any new FastAPI route must be added here as well.
```

- [ ] **Step 3.2: Validate the Caddyfile.**

```bash
docker run --rm -v "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2-alpine \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Expected: `Valid configuration`.

If invalid, the most likely cause is a stray tab/space mismatch — caddy is whitespace-sensitive. Compare against the diff carefully.

- [ ] **Step 3.3: Rebuild the frontend image to verify the new Caddyfile lands inside it.**

```bash
docker build -f docker/Dockerfile.frontend -t sepsis-atlas-frontend:test . 2>&1 | tail -5
docker run --rm sepsis-atlas-frontend:test \
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Expected: `Valid configuration`.

- [ ] **Step 3.4: Commit.**

```bash
git add deploy/Caddyfile
git commit -m "feat(caddy): wire Caddyfile for in-container deploy

root → /srv (image-baked Astro dist), reverse_proxy → backend:8000
(compose service DNS), drop the file logger (docker captures stdout).
@api matcher unchanged; PDFs and JSON ship inside the image and fall
through to file_server."
```

---

## Task 4: Add the frontend service to docker-compose.prod.yml

**Files:**
- Modify: `docker-compose.prod.yml`

Changes:

- Add a `frontend` service that pulls `ghcr.io/orfrederick/sepsis-atlas-frontend:main`, exposes 80/443/443udp, mounts the two caddy state volumes and the host conf.d dir.
- Drop the backend's `ports: 127.0.0.1:8000:8000` mapping — caddy reaches the backend over the compose network now, nothing else needs port 8000 on the host.
- Add `caddy_data` and `caddy_config` named volumes at the bottom.

- [ ] **Step 4.1: Edit `docker-compose.prod.yml`.**

Replace the `services:` block with the following. The full file should look like this after the edit (compare against the existing file and apply the edits, don't blindly overwrite — the backend block has comments worth preserving):

```yaml
# Production override.
#
# Applied with:
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main up -d
#
# Differences from dev compose:
#   - Backend is pulled from ghcr.io (built by .github/workflows/deploy.yml),
#     never built on the host. The local `build:` directive is reset.
#   - Backend is only reachable over the compose network; no host port mapping.
#   - Source-code volume mount dropped (no hot reload in prod).
#   - Frontend container (caddy + Astro dist + PDFs + JSON, all baked in)
#     terminates TLS on :80/:443 and reverse-proxies API routes to backend.
#   - Watchtower watches both labeled containers and pulls/restarts whenever
#     a new image tag appears in ghcr.

services:
  backend:
    build: !reset null
    image: ghcr.io/orfrederick/sepsis-atlas-backend:main
    # `missing` so a transient ghcr outage during deploy doesn't recreate
    # the container into a hole. Ongoing image refreshes are owned by the
    # Watchtower sidecar below, which polls ghcr every 5 minutes.
    pull_policy: missing
    labels:
      com.centurylinklabs.watchtower.enable: "true"
    volumes: !override
      - ./data:/app/data
      - ./db.sqlite:/app/db.sqlite
      - ./static:/app/static
      - ./runs:/app/runs
      - ./logs:/app/logs
    ports: !reset null

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

  watchtower:
    image: containrrr/watchtower:1.7.1
    container_name: atlas-watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      # Mount the deploy user's docker creds (not root's). The deploy user
      # is in the docker group and is what runs `docker login` on the VPS
      # if the ghcr package ever goes private; root's config is empty.
      - /home/deploy/.docker/config.json:/config.json:ro
    environment:
      WATCHTOWER_LABEL_ENABLE: "true"   # only update containers with the label above
      WATCHTOWER_CLEANUP: "true"        # delete old images after upgrade
      WATCHTOWER_POLL_INTERVAL: "300"   # 5 minutes
      WATCHTOWER_INCLUDE_RESTARTING: "true"
    networks: [sepsis]

volumes:
  caddy_data:
  caddy_config:
```

The key edits versus the prior file:

1. Backend `ports:` was `!override - "127.0.0.1:8000:8000"`; becomes `!reset null` (port mapping removed entirely).
2. New `frontend` service inserted after backend.
3. New `volumes:` top-level key added at the bottom.
4. The file header comment is updated to mention the frontend.

- [ ] **Step 4.2: Validate the merged compose config.**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main config 2>&1 | tail -40
```

Expected: prints the merged config with three services (`backend`, `frontend`, `watchtower`). No `ports` under `backend` (or if present, empty). `frontend` has `ports: 80, 443, 443/udp`. `volumes:` section at bottom lists `caddy_data`, `caddy_config`.

If you see `WARN[0000] /path/to/docker-compose.prod.yml: 'services.backend.ports.0' must be a string`, the `!reset null` form isn't supported on your compose version — replace `ports: !reset null` with `ports: !override []` (empty list).

- [ ] **Step 4.3: Commit.**

```bash
git add docker-compose.prod.yml
git commit -m "feat(compose): add frontend service, drop backend host port

New frontend service runs the ghcr-built caddy image, terminates TLS on
80/443, reverse-proxies API to backend over the compose network. Backend
no longer needs to be reachable on the host — drop the 127.0.0.1:8000
mapping. Two named volumes (caddy_data, caddy_config) persist LE certs
and ACME state across container restarts."
```

---

## Task 5: Update the GitHub Actions workflow

**Files:**
- Modify: `.github/workflows/deploy.yml`

Changes:

- Header comment: drop the bullet about SSHing to the VPS; mention the second image.
- `changes` job: add a `frontend` output tracking `web/**`, `docker/Dockerfile.frontend`, `deploy/Caddyfile`, `data/papers/raw/**`.
- Add a `build-frontend` job mirroring `build-backend`, with `if:` gated on the new frontend filter.
- Delete the `deploy` job entirely.

- [ ] **Step 5.1: Replace `.github/workflows/deploy.yml` with the following.**

```yaml
name: deploy main

# On every push to main (except docs-only):
#   1. detect whether backend and/or frontend code changed
#   2. build + push whichever images changed to ghcr
#
# Watchtower on the VPS polls ghcr every 5 minutes and restarts the
# matching container when a new :main tag lands, so this workflow does
# not need to trigger anything on the VPS directly.

on:
  push:
    branches: [main]
    paths-ignore:
      - '**.md'
      - 'docs/**'
  workflow_dispatch:

concurrency:
  group: deploy-main
  cancel-in-progress: false

permissions:
  contents: read
  packages: write

env:
  # Hardcoded lowercase — docker tags reject mixed-case (`OrFrederick`).
  # Keep in sync with `image:` in docker-compose.prod.yml.
  BACKEND_IMAGE: ghcr.io/orfrederick/sepsis-atlas-backend
  FRONTEND_IMAGE: ghcr.io/orfrederick/sepsis-atlas-frontend

jobs:
  changes:
    runs-on: ubuntu-latest
    outputs:
      backend: ${{ steps.filter.outputs.backend }}
      frontend: ${{ steps.filter.outputs.frontend }}
    steps:
      - uses: actions/checkout@v4
      - id: filter
        uses: dorny/paths-filter@v3
        with:
          filters: |
            backend:
              - 'src/**'
              - 'docker/Dockerfile.backend'
              - 'pyproject.toml'
              - '.dockerignore'
            frontend:
              - 'web/**'
              - 'docker/Dockerfile.frontend'
              - 'deploy/Caddyfile'
              - 'data/papers/raw/**'
              - '.dockerignore'

  build-backend:
    needs: changes
    if: needs.changes.outputs.backend == 'true' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: build + push backend
        uses: docker/build-push-action@v5
        with:
          context: .
          file: docker/Dockerfile.backend
          push: true
          tags: |
            ${{ env.BACKEND_IMAGE }}:main
            ${{ env.BACKEND_IMAGE }}:${{ github.sha }}
          cache-from: type=gha,scope=backend
          cache-to: type=gha,mode=max,scope=backend

  build-frontend:
    needs: changes
    if: needs.changes.outputs.frontend == 'true' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: build + push frontend
        uses: docker/build-push-action@v5
        with:
          context: .
          file: docker/Dockerfile.frontend
          push: true
          tags: |
            ${{ env.FRONTEND_IMAGE }}:main
            ${{ env.FRONTEND_IMAGE }}:${{ github.sha }}
          cache-from: type=gha,scope=frontend
          cache-to: type=gha,mode=max,scope=frontend
```

The cache `scope=backend` / `scope=frontend` change is intentional — separate caches per image avoid one job evicting the other's cached layers.

- [ ] **Step 5.2: Lint the workflow YAML.**

```bash
# actionlint catches schema + expression errors that pure YAML parsers miss.
# If actionlint isn't installed: `brew install actionlint` or skip this step.
actionlint .github/workflows/deploy.yml 2>&1 | head -20 || echo "(actionlint not installed; falling back to yamllint)"
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))" && echo "YAML parse OK"
```

Expected: no errors from actionlint (if installed), `YAML parse OK` from the python check.

- [ ] **Step 5.3: Commit.**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: build+push frontend image, drop SSH deploy job

Adds build-frontend mirroring build-backend (triggered by web/, the new
Dockerfile.frontend, the Caddyfile, or raw PDF changes). Deletes the
deploy job entirely — Watchtower already pulls fresh images from ghcr,
no remote shell step needed. DEPLOY_SSH_* secrets become unused; remove
from repo settings after the cutover succeeds."
```

---

## Task 6: Create `update-compose.sh` for on-VPS compose refreshes

**Files:**
- Create: `update-compose.sh` (repo root)

A 12-line script that curls fresh compose files from raw.githubusercontent.com and bounces the stack. Committed so it's version-controlled, fetched from the same URL pattern during the cutover.

- [ ] **Step 6.1: Create `update-compose.sh`.**

```bash
#!/usr/bin/env bash
# Refresh the compose files on the VPS from main and restart the stack.
# Run from /opt/sepsisatlas as the deploy user (must be in the docker group).
#
# The VPS has no git checkout. This script is the only way infra-shaped
# changes (compose YAML, this script itself) reach prod after the initial
# bootstrap. Image updates flow separately via Watchtower polling ghcr.
set -euo pipefail

RAW="https://raw.githubusercontent.com/OrFrederick/SepsisAtlas/main"
PROJECT="atlas-main"

cd /opt/sepsisatlas
curl -fsSL "$RAW/docker-compose.yml"      -o docker-compose.yml
curl -fsSL "$RAW/docker-compose.prod.yml" -o docker-compose.prod.yml
curl -fsSL "$RAW/update-compose.sh"       -o update-compose.sh
chmod +x update-compose.sh

docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" up -d --remove-orphans
```

- [ ] **Step 6.2: Make it executable and shellcheck it.**

```bash
chmod +x update-compose.sh
shellcheck update-compose.sh 2>&1 | head -20 || echo "(shellcheck not installed; visual check it instead)"
```

Expected: no warnings. If shellcheck isn't installed and you don't want to install it, visually verify there are no obvious issues (unquoted variables, missing `set -e`, etc.).

- [ ] **Step 6.3: Commit.**

```bash
git add update-compose.sh
git commit -m "feat(deploy): add update-compose.sh for on-VPS compose refreshes

The VPS no longer has a git checkout — only the two compose YAMLs and
this script live in /opt/sepsisatlas. The script curls fresh copies
from raw.githubusercontent.com/.../main and bounces the stack. Used
during the cutover, and any future change to either compose file."
```

---

## Task 7: Strip caddy + bun + narrow sudoers from `deploy/bootstrap.sh`

**Files:**
- Modify: `deploy/bootstrap.sh`

The script provisioned a host caddy install, bun on the VPS for the local astro build, and a narrow sudoers rule that whitelisted the commands `deploy-main.sh` needed (rsync, install, cp, systemctl reload caddy). Everything in that list is dead now. The script also created `/var/www/atlas-main` and `/etc/caddy/conf.d`; both go away too. New layout creates `/etc/sepsisatlas/caddy-conf.d` instead.

What stays: deploy user, ssh hardening, ufw (still need 80/443 open for the container), fail2ban, unattended-upgrades, docker install + deploy-user-in-docker-group, hostname/timezone.

- [ ] **Step 7.1: Edit `deploy/bootstrap.sh`.**

Delete the entire `install_caddy()` function (lines 109–118 in the current file, the function starting with `install_caddy() {`):

```bash
install_caddy() {
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  ...
  systemctl enable --now caddy
}
```

Delete the entire `install_bun()` function (lines 120–128):

```bash
install_bun() {
  ...
}
```

Replace the entire `prepare_dirs()` function with:

```bash
prepare_dirs() {
  install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 755 /opt/sepsisatlas
  install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 755 /etc/sepsisatlas/caddy-conf.d
  # The deploy user no longer needs any narrow sudo rules — host caddy is gone,
  # all infra changes go through docker compose, and the deploy user is in the
  # docker group (granted by install_docker above).
  rm -f /etc/sudoers.d/deploy-caddy
}
```

In `main()`, remove these lines:

```bash
  install_caddy
  install_bun
```

Also update the top-of-file comment block to reflect the new responsibility set:

```bash
#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 24.04 host for SepsisAtlas.
#
# Run as root on the target machine:
#   ssh root@<host> bash -s < deploy/bootstrap.sh
#
# After this script runs, the host has:
#   - a `deploy` user with the ops team's SSH keys + docker group membership
#   - docker engine + compose plugin
#   - ufw allowing 22/80/443, fail2ban watching sshd, unattended-upgrades
#   - /opt/sepsisatlas/ ready for the compose files (fetched separately)
#   - /etc/sepsisatlas/caddy-conf.d/ ready for private caddy directives
#
# Idempotent — re-running on a configured host is safe.
```

Also: in `apt-get install -y git curl ca-certificates gnupg lsb-release rsync unzip` (in `main()`), `rsync` is no longer needed for any host-side workflow. Leave it; it's small and is occasionally useful for ad-hoc VPS operations. No change to that line.

- [ ] **Step 7.2: Shellcheck the result.**

```bash
shellcheck deploy/bootstrap.sh 2>&1 | head -20 || echo "(shellcheck not installed; visual check)"
```

Expected: no new warnings.

- [ ] **Step 7.3: Commit.**

```bash
git add deploy/bootstrap.sh
git commit -m "chore(deploy): strip caddy + bun + narrow sudoers from bootstrap

Host caddy, host bun, /var/www, /etc/caddy/conf.d, and the narrow
sudoers rule that whitelisted deploy-main.sh's commands all go away —
nothing runs them anymore. Replace with /etc/sepsisatlas/caddy-conf.d
for private caddy overrides bind-mounted into the container. ufw/fail2ban
/sshd-hardening/docker/deploy-user setup all stay."
```

---

## Task 8: Delete `deploy/deploy-main.sh`

**Files:**
- Delete: `deploy/deploy-main.sh`

The script's only caller was the GitHub Actions deploy job, which is gone. Nothing else references it.

- [ ] **Step 8.1: Confirm there are no other references.**

```bash
grep -rn "deploy-main\.sh" --exclude-dir=.git . | grep -v docs/superpowers/
```

Expected output: empty (no callers left after Task 5 deleted the workflow's reference). Spec/plan docs under `docs/superpowers/` are allowed to mention it for rollback purposes.

- [ ] **Step 8.2: Remove the file.**

```bash
git rm deploy/deploy-main.sh
```

- [ ] **Step 8.3: Commit.**

```bash
git commit -m "chore(deploy): remove deploy-main.sh

Its sole caller (.github/workflows/deploy.yml's deploy job) is gone.
Backend ships via Watchtower polling ghcr; frontend ships the same way
now. Compose-file changes flow via update-compose.sh. The old script's
behaviour (frontend rebuild, caddy reload) is replaced wholesale by the
new frontend image + bind-mounted conf.d. Kept in git history for
rollback reference."
```

---

## Task 9: Local end-to-end smoke test

**Files:** none changed; verification only.

Bring up the prod stack locally with the freshly-built images and confirm the frontend serves something on `:80`. We can't test TLS locally (no DNS for `atlas.efferon.com`), but we can verify:

- The compose merge is healthy.
- The frontend container starts, caddy doesn't crash on the Caddyfile.
- Hitting `http://localhost/` returns the Astro homepage.
- Hitting `http://localhost/health` returns the backend's 200.

- [ ] **Step 9.1: Tag the local frontend image so compose can find it.**

The prod compose pins `ghcr.io/orfrederick/sepsis-atlas-frontend:main`. For local testing, re-tag the just-built `sepsis-atlas-frontend:test`:

```bash
docker tag sepsis-atlas-frontend:test ghcr.io/orfrederick/sepsis-atlas-frontend:main
```

Do the same for the backend if you don't already have it pulled:

```bash
docker pull ghcr.io/orfrederick/sepsis-atlas-backend:main
```

- [ ] **Step 9.2: Bring up the stack on alternate ports to avoid host-port collisions.**

Create a one-off override (don't commit this) that maps 8080→80 and skips 443:

```bash
cat > /tmp/atlas-local.yml <<'EOF'
services:
  frontend:
    ports: !override
      - "8080:80"
EOF
```

Run:

```bash
docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml -f /tmp/atlas-local.yml \
  -p atlas-local up -d --remove-orphans
```

Wait ~15 seconds for backend's healthcheck to pass and frontend to start.

```bash
docker ps --filter "label=com.docker.compose.project=atlas-local"
```

Expected: 3 containers running (`atlas-local-backend-1`, `atlas-local-frontend-1`, `atlas-watchtower`). If `frontend` isn't running, `docker logs atlas-local-frontend-1` will say why; common: backend healthcheck not passing because the local data volume is empty.

- [ ] **Step 9.3: Hit the endpoints.**

```bash
curl -fsS http://localhost:8080/ | head -5
curl -fsS http://localhost:8080/health
curl -fsSI http://localhost:8080/pdfs/Baloch_2022.pdf | head -3
```

Expected:

- `/` returns HTML starting with `<!doctype html>` or similar (Astro homepage).
- `/health` returns `{"ok":true,...}`.
- `/pdfs/Baloch_2022.pdf` returns headers including `HTTP/1.1 200 OK` and `Content-Type: application/pdf`.

If `/pdfs/...` returns 404, the PDF rsync inside the Dockerfile didn't land — re-check Task 1 (`.dockerignore`) and Task 2 (Dockerfile).

If `/health` returns 502 or "no upstream", caddy can't reach the backend service. Check that both containers are on the `sepsis` network: `docker network inspect atlas-local_sepsis`.

- [ ] **Step 9.4: Tear down.**

```bash
docker compose -p atlas-local down -v
rm /tmp/atlas-local.yml
```

The `-v` removes the local `caddy_data`/`caddy_config` volumes so subsequent local runs don't carry state.

- [ ] **Step 9.5: No commit. This was verification only.**

---

## Task 10: Push the branch and open a PR

**Files:** none changed; git operations only.

- [ ] **Step 10.1: Confirm branch state.**

```bash
git log --oneline dev..HEAD
git diff --stat dev..HEAD
```

Expected: ~8 commits (one per task above plus the spec commits already on the branch), touching `docker/Dockerfile.frontend`, `docker-compose.prod.yml`, `deploy/Caddyfile`, `deploy/bootstrap.sh`, `.dockerignore`, `.github/workflows/deploy.yml`, `update-compose.sh`, removing `deploy/deploy-main.sh`, plus the spec + plan docs.

- [ ] **Step 10.2: Push the branch.**

```bash
git push -u origin deploy-changes
```

- [ ] **Step 10.3: Open the PR against `dev`.**

```bash
gh pr create --base dev --title "deploy: containerize frontend, remove CI-SSH deploy" \
  --body "$(cat <<'EOF'
## Summary

Replaces the GitHub Actions → VPS SSH deploy with a second ghcr-built image
(frontend = caddy + Astro `dist` + PDFs + JSON, all baked in). Watchtower
handles updates the same way it already does for the backend.

After this lands and the cutover runs, the VPS has no host services beyond
docker itself, no source checkout, and no inbound SSH from CI.

- Design: `docs/superpowers/specs/2026-05-22-containerize-frontend-design.md`
- Plan: `docs/superpowers/plans/2026-05-22-containerize-frontend.md`

## What changed

- New `docker/Dockerfile.frontend` — multi-stage bun → caddy:2-alpine.
- New `.github/workflows/deploy.yml` `build-frontend` job; `deploy` (SSH) job deleted.
- New `frontend` service in `docker-compose.prod.yml`; backend host port mapping removed.
- New `update-compose.sh` for the rare case compose YAML itself changes.
- `deploy/Caddyfile` rewired for in-container routing (`backend:8000`, `root /srv`).
- `deploy/bootstrap.sh` stripped of caddy/bun/narrow-sudoers.
- `deploy/deploy-main.sh` deleted.
- `.dockerignore` updated to whitelist Caddyfile + raw PDFs.

## Cutover (one-time, after merge to main)

Runbook in the design doc, section "Cutover plan". Summary: stop host
caddy, move state dirs out of the soon-to-delete clone, fetch compose
files via curl, `docker compose up -d`. ~30-60s of TLS unavailability
during the LE re-issue.

## Test plan

- [x] `docker build -f docker/Dockerfile.frontend` succeeds locally.
- [x] `caddy validate` passes on the Caddyfile inside the image.
- [x] `docker compose ... config` merges cleanly with both services.
- [x] Local stack smoke test (Task 9 in the plan): `/`, `/health`, `/pdfs/<stem>.pdf` all return 200.
- [ ] Once merged: confirm CI builds both images and pushes `:main` + `:<sha>` tags to ghcr.
- [ ] VPS cutover per the design doc runbook.
- [ ] Post-cutover: `/health`, a paper page, `/data/manifest.json`, and a `/pdfs/<stem>.pdf` all return 200 over HTTPS.

## Notes

- `DEPLOY_SSH_HOST`, `DEPLOY_SSH_USER`, `DEPLOY_SSH_KEY` repo secrets become unused. Recommend removing them from repo settings after the cutover succeeds.
- One-time data bootstrap (committing `web/public/data/*.json`) was done as a pre-flight step before this PR — see commit "data: bootstrap web exports for CI builds" early in the branch.
EOF
)"
```

- [ ] **Step 10.4: Hand the PR URL back to the user.**

`gh pr create` prints the URL on stdout. Surface it.

---

## After the PR merges (not part of this plan)

The cutover runbook lives in the design doc, section "Cutover plan (one-time, on prod VPS)". It is **not** part of this plan because it runs on prod, not in the worktree. The user executes it manually after this PR merges to `main` and CI publishes both images to ghcr.

## Self-review notes

Spec coverage:

- "Backend serves all dynamic data" → reframed to "data committed to repo, baked into image"; PDFs and JSON live in the image, no new backend routes. ✓ (Tasks 1, 2)
- "Combined caddy + Astro image" → ✓ (Task 2)
- "Compose service shape" → ✓ (Task 4)
- "TLS" → handled implicitly: caddy auto-issues, named volume persists. ✓ (Task 4)
- "CI workflow" → ✓ (Task 5)
- "bootstrap.sh" → ✓ (Task 7)
- "Deletions" → `deploy-main.sh` (Task 8); `/var/www/atlas-main` and `/opt/sepsisatlas/main/` are VPS-side cutover steps, not in this plan.
- "VPS filesystem layout" + "update-compose.sh" → ✓ (Task 6)
- "Cutover plan" → not part of this plan by design; lives in the spec.

Placeholder scan: no TBDs, no "handle edge cases" hand-waves, no "similar to Task N" without explicit code. Every step that changes code shows the code.

Type consistency: image tag `ghcr.io/orfrederick/sepsis-atlas-frontend:main` is referenced identically in Dockerfile context-free, in compose, in workflow env vars (`FRONTEND_IMAGE`), and in the update-compose runbook. Caddy reverse_proxy target `backend:8000` matches the compose service name `backend` exactly.
