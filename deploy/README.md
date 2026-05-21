# SepsisAtlas — deploy ops

Production target: `atlas.efferon.com` (DigitalOcean droplet, Ubuntu 24.04).

## What runs where

| Component         | Where                                  | Port           |
| ----------------- | -------------------------------------- | -------------- |
| Caddy             | host, systemd unit `caddy.service`     | 80, 443        |
| Backend           | docker compose project `atlas-main`, image pulled from ghcr | 127.0.0.1:8000 |
| Neo4j             | docker compose project `atlas-main`    | internal only  |
| Watchtower        | docker compose project `atlas-main`    | internal only  |
| Astro static SPA  | /var/www/atlas-main (served by Caddy)  | n/a            |

## Build / deploy flow

`.github/workflows/deploy.yml` runs on every push to main (except docs-only):

```
push to main
   │
   ├─ job: changes        detect whether src/, Dockerfile, pyproject, or .dockerignore changed
   │
   ├─ job: build-backend  (only if backend changed, or workflow_dispatch)
   │     └─ docker build --push  →  ghcr.io/orfrederick/sepsis-atlas-backend:main + :<sha>
   │
   └─ job: deploy         (waits for build-backend; skips it if not needed)
         └─ ssh deploy@vps  →  deploy-main.sh
                ├─ git fetch + reset --hard origin/main
                ├─ bun install + build web  →  rsync /var/www/atlas-main
                ├─ docker compose up -d   (pulls backend image if missing; idempotent otherwise)
                └─ caddy reload
```

Independently, **Watchtower** runs on the VPS as a compose sidecar. It polls
ghcr every 5 minutes and, when `:main` advances, pulls the new image and
restarts `atlas-main-backend-1`. This is what makes "backend code lands on
main → VPS picks it up" automatic — the SSH deploy step itself only pulls
the image when missing, so it doesn't race with the build.

Backend image is **never built on the VPS**. Frontend is built on the VPS
(bun build is ~13s; not worth the registry round-trip).

### First-time bootstrap (after merging the PR that adds this workflow)

The first run needs the image to exist before compose can `up` the new
backend service definition. Trigger the build once manually:

```
gh workflow run "deploy main" -R OrFrederick/SepsisAtlas
```

(`workflow_dispatch` forces the build-backend job to run even if no backend
files changed in the latest commit.) After that, every push to main runs
the full chain automatically.

## Initial bootstrap (one-shot, fresh server)

```bash
ssh root@<new-host> bash -s < deploy/bootstrap.sh
```

Then run the first deploy:

```bash
ssh deploy@<host> /opt/sepsisatlas/main/deploy/deploy-main.sh
```

## Updating .env

`/opt/sepsisatlas/main/.env` holds `OPENROUTER_API_KEY` and optional Langfuse keys.
Edit in place, then:

```bash
ssh deploy@<host> 'cd /opt/sepsisatlas/main && docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main restart backend'
```

## Logs

- Caddy access log: `/var/log/caddy/atlas.efferon.com.access.log`
- Caddy service log: `journalctl -u caddy`
- Backend logs: `docker compose -p atlas-main logs -f backend`
- SSH ban state: `sudo fail2ban-client status sshd`

## Rolling back

```bash
ssh deploy@<host> bash -c 'cd /opt/sepsisatlas/main && git fetch && git reset --hard <good-sha> && deploy/deploy-main.sh'
```

## GHCR package visibility

After the first run of `build-backend.yml`, the `sepsis-atlas-backend` package
appears under `https://github.com/users/orfrederick/packages`. By default it
inherits the repo's visibility (public). If you ever flip the repo private,
either set the package to public manually or run `docker login ghcr.io` on
the VPS as the `deploy` user with a PAT that has `read:packages` — Watchtower
bind-mounts `/home/deploy/.docker/config.json` and will pick up those creds.

## GitHub Actions deploy

The workflow at `.github/workflows/deploy.yml` SSHes to the server as `deploy@<host>`
with a key stored in repo secrets:

| Secret             | Value                                                       |
| ------------------ | ----------------------------------------------------------- |
| `DEPLOY_SSH_HOST`  | server public IP                                            |
| `DEPLOY_SSH_USER`  | `deploy`                                                    |
| `DEPLOY_SSH_KEY`   | private key whose public half is in `~deploy/.ssh/authorized_keys` |

Repo is public, so the server clones over HTTPS — no GitHub deploy key needed.

## Common failures

- **HTTPS won't issue**: `journalctl -u caddy | grep -i error`. Usually DNS doesn't point at the server or port 80 is blocked.
- **Backend 502 at /query**: `docker compose -p atlas-main logs --tail=200 backend`. Common cause: missing or invalid `OPENROUTER_API_KEY`.
- **`bun: command not found` during deploy**: the `/usr/local/bin/bun` symlink may be missing; re-run the `install_bun` section of `bootstrap.sh`.

## Isolation model

- **Prod** is this server (`atlas.efferon.com`). Long-lived state lives in `/opt/sepsisatlas/main/db.sqlite`, `data/papers/raw/`, `runs/`, `logs/`.
- **Dev** runs on the contributor's laptop with their own `.env` and `.venv` against a local `db.sqlite`. No shared state, no shared backend.
- **PR previews** are not deployed. We considered both subdomain (`pr-<N>.atlas.efferon.com`) and path-prefix (`/pr/<N>/`); the former needs wildcard DNS we don't have, the latter shares browser origin and `/static` with prod, which doesn't qualify as real isolation. If wildcard DNS or DNS-01 API access becomes available, re-add the per-PR scripts (see git history of this directory).
