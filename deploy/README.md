# SepsisAtlas — deploy ops

Production target: `atlas.efferon.com` (DigitalOcean droplet, Ubuntu 24.04).

## What runs where

| Component         | Where                                  | Port           |
| ----------------- | -------------------------------------- | -------------- |
| Caddy             | host, systemd unit `caddy.service`     | 80, 443        |
| Backend           | docker compose project `atlas-main`    | 127.0.0.1:8000 |
| Neo4j             | docker compose project `atlas-main`    | internal only  |
| Astro static SPA  | /var/www/atlas-main (served by Caddy)  | n/a            |

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
