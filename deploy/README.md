# SepsisAtlas — deploy ops

Production target: `atlas.efferon.com` (DigitalOcean droplet, Ubuntu 24.04).

## What runs where

Everything in prod is docker. The VPS has no source checkout and no host services beyond docker itself.

| Component  | Where                                                    | Port            |
| ---------- | -------------------------------------------------------- | --------------- |
| Caddy + frontend | docker compose project `atlas-main` (image from ghcr) | 80, 443         |
| Backend    | docker compose project `atlas-main` (image from ghcr)    | internal only   |
| Watchtower | docker compose project `atlas-main`                      | internal only   |

The frontend image is `caddy:2-alpine` + the built Astro `dist/` + PDFs + JSON data, all baked in at CI build time. The Caddyfile is baked in too and reverse-proxies API paths (`/query`, `/viewer/*`, `/papers/*/pdf`, `/health`, `/phenotypes`, `/static/*`, etc.) to the backend over the internal compose network.

## Build / deploy flow

`.github/workflows/deploy.yml` runs on every push to `main` (except docs-only):

```
push to main
   │
   ├─ job: changes        detect whether backend/frontend code changed
   │
   ├─ job: build-backend  (only if backend changed, or workflow_dispatch)
   │     └─ docker build --push → ghcr.io/orfrederick/sepsis-atlas-backend:main + :<sha>
   │
   └─ job: build-frontend (only if frontend changed, or workflow_dispatch)
         └─ docker build --push → ghcr.io/orfrederick/sepsis-atlas-frontend:main + :<sha>
```

No SSH step. **Watchtower** runs on the VPS as a compose sidecar; it polls ghcr every 5 minutes and, when either `:main` tag advances, pulls the new image and restarts that container. Both `backend` and `frontend` containers carry the `com.centurylinklabs.watchtower.enable: "true"` label.

Both images are **always built in CI**, never on the VPS. The frontend's CI build bundles the PDFs from `data/papers/raw/` and the JSON exports from `web/public/data/` directly into the image — those files have to be committed to the repo before they reach prod.

### Updating either compose file

Compose YAML changes don't ship via the registry; they live in `/opt/sepsisatlas/` on the box. To pick up new compose files after a merge:

```bash
ssh efferon-deploy 'cd /opt/sepsisatlas && ./update-compose.sh'
```

The script curls fresh `docker-compose.yml`, `docker-compose.prod.yml`, and itself from `raw.githubusercontent.com/.../main`, then `docker compose pull && up -d --remove-orphans`. The script is committed at the repo root; it's the one escape hatch for infra changes that aren't image-shaped.

## Initial bootstrap (one-shot, fresh server)

Provision the host with docker + ufw + fail2ban + the deploy user:

```bash
ssh root@<new-host> bash -s < deploy/bootstrap.sh
```

Then drop the compose files onto the box and bring up the stack:

```bash
ssh deploy@<host> bash -lc '
  RAW=https://raw.githubusercontent.com/OrFrederick/SepsisAtlas/main
  cd /opt/sepsisatlas
  curl -fsSL "$RAW/docker-compose.yml"      -o docker-compose.yml
  curl -fsSL "$RAW/docker-compose.prod.yml" -o docker-compose.prod.yml
  curl -fsSL "$RAW/update-compose.sh"       -o update-compose.sh
  chmod +x update-compose.sh
  # Place .env (with OPENROUTER_API_KEY etc.) at /opt/sepsisatlas/.env before continuing.
  docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main up -d
'
```

The first push to `main` after this will trigger CI image builds; Watchtower picks them up on its next 5-minute poll. To force the first build without waiting for a code change:

```bash
gh workflow run "deploy main" -R OrFrederick/SepsisAtlas
```

## Updating .env

`/opt/sepsisatlas/.env` holds `OPENROUTER_API_KEY` and optional Langfuse keys. Edit in place, then:

```bash
ssh efferon-deploy 'cd /opt/sepsisatlas && docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main restart backend'
```

## Feedback feature

The feedback form (`/feedback`) creates labeled GitHub issues via the GitHub
REST API. Required production env vars:

- `GITHUB_FEEDBACK_TOKEN` — fine-grained PAT, scoped to `Issues: read & write`
  on `OrFrederick/SepsisAtlas` only. Set 1-year expiry; rotate annually.
- `GITHUB_FEEDBACK_REPO` — usually `OrFrederick/SepsisAtlas`.
- `FEEDBACK_ALLOWED_ORIGIN` — comma-separated list of allowed `Origin`/`Referer`
  prefixes for form submissions. In prod, set to the public hostname.

Run `scripts/setup-feedback-labels.sh` once after deploy to seed the
required labels. Triage board: https://github.com/users/OrFrederick/projects/2

## Private caddy directives

If you need basic-auth, IP allow-lists, or any other server-side-only caddy snippets that can't go in the public image, drop `.caddy` files into `/etc/sepsisatlas/caddy-conf.d/` on the VPS. The frontend container bind-mounts that dir as read-only at `/etc/caddy/conf.d/`, and the baked Caddyfile imports everything in it. Restart the frontend container to pick up changes.

## Logs

- Frontend (caddy access + service): `docker compose -p atlas-main logs -f frontend`
- Backend logs: `docker compose -p atlas-main logs -f backend`
- Watchtower (when it pulled what): `docker logs -f atlas-watchtower`
- SSH ban state: `sudo fail2ban-client status sshd`

Docker's `json-file` log driver handles rotation. There's no persistent access-log file on the host anymore.

## Rolling back

Both images are tagged with the commit SHA in addition to `:main`. To pin the stack at a known-good SHA:

```bash
ssh efferon-deploy 'bash -lc "
  cd /opt/sepsisatlas
  SHA=<good-sha>
  docker pull ghcr.io/orfrederick/sepsis-atlas-backend:\$SHA
  docker pull ghcr.io/orfrederick/sepsis-atlas-frontend:\$SHA
  docker tag  ghcr.io/orfrederick/sepsis-atlas-backend:\$SHA  ghcr.io/orfrederick/sepsis-atlas-backend:main
  docker tag  ghcr.io/orfrederick/sepsis-atlas-frontend:\$SHA ghcr.io/orfrederick/sepsis-atlas-frontend:main
  docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main up -d
"'
```

Re-tagging the local `:main` ref stops Watchtower from immediately racing the rollback. The next CI build on `main` will overwrite the registry's `:main` again — push a revert commit if you want the rollback to stick across the next build.

## GHCR package visibility

After the first run of each build job, the `sepsis-atlas-backend` and `sepsis-atlas-frontend` packages appear under `https://github.com/users/orfrederick/packages`. They inherit the repo's visibility (currently public). If you ever flip the repo private, either set the packages to public manually or run `docker login ghcr.io` on the VPS as the `deploy` user with a PAT that has `read:packages` — Watchtower bind-mounts `/home/deploy/.docker/config.json` and will pick up those creds.

## Common failures

- **HTTPS won't issue (first run after cutover):** `docker logs -f atlas-main-frontend-1 | grep -i acme`. Usually DNS doesn't point at the server or port 80/443 is blocked. The `caddy_data` named volume must be writable.
- **Backend 502 at /query:** `docker compose -p atlas-main logs --tail=200 backend`. Common cause: missing or invalid `OPENROUTER_API_KEY` in `/opt/sepsisatlas/.env`.
- **Frontend shows empty tables / 404s on PDFs:** the build that produced the running `:main` image was missing `web/public/data/*.json` or `data/papers/raw/*.pdf` in the CI checkout. Confirm those files are committed; the next build will bake them in.
- **Watchtower not pulling new images:** `docker logs atlas-watchtower`. Most often: the container the label was supposed to be on isn't running, or the poll interval (5 min) hasn't elapsed yet.

## Isolation model

- **Prod** is this server (`atlas.efferon.com`). Long-lived state lives under `/opt/sepsisatlas/` on the VPS: `db.sqlite`, `data/papers/raw/`, `runs/`, `logs/`, `static/`. The bind-mount layout in `docker-compose.prod.yml` is the source of truth for which directories the backend container needs at runtime.
- **Dev** runs on the contributor's laptop with their own `.env` and `.venv` against a local `db.sqlite`. No shared state, no shared backend.
- **PR previews** are not deployed. We considered both subdomain (`pr-<N>.atlas.efferon.com`) and path-prefix (`/pr/<N>/`); the former needs wildcard DNS we don't have, the latter shares browser origin and `/static` with prod, which doesn't qualify as real isolation. If wildcard DNS or DNS-01 API access becomes available, re-add the per-PR scripts (see git history of this directory).
