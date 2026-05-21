# SepsisAtlas — deploy ops

Production target: `atlas.efferon.com` (DigitalOcean droplet, Ubuntu 24.04).
PR previews: `atlas.efferon.com/pr/<N>/` (path-prefixed; no wildcard DNS, single TLS cert).

## What runs where

| Component          | Where                                  | Port (loopback)      |
| ------------------ | -------------------------------------- | -------------------- |
| Caddy              | host, systemd unit `caddy.service`     | 80, 443              |
| Backend (main)     | docker compose project `atlas-main`    | 127.0.0.1:8000       |
| Neo4j (main)       | docker compose project `atlas-main`    | internal only        |
| Backend (PR #N)    | docker compose project `atlas-pr-N`    | 127.0.0.1:(8100 + N % 800) |
| Astro build (main) | /var/www/atlas-main (served by Caddy)  | n/a                  |
| Astro build (PR)   | /var/www/atlas-pr-N (served by Caddy at /pr/N/, base path baked into the Astro build) | n/a                  |

## Initial bootstrap (one-shot, fresh server)

```bash
ssh root@<new-host> bash -s < deploy/bootstrap.sh
```

Then install the GitHub Actions deploy key (see below) and run the first deploy:

```bash
ssh deploy@<host> /opt/sepsisatlas/main/deploy/deploy-main.sh
```

## Updating .env

`/opt/sepsisatlas/main/.env` holds `OPENROUTER_API_KEY` and optional Langfuse keys.
Edit in place, then:

```bash
ssh deploy@<host> 'cd /opt/sepsisatlas/main && docker compose -f docker-compose.yml -f docker-compose.prod.yml -p atlas-main restart backend'
```

PR previews inherit main's `.env` at deploy time.

## Logs

- Caddy access logs: `/var/log/caddy/atlas.efferon.com.access.log`, `/var/log/caddy/pr-N.atlas.efferon.com.access.log`
- Caddy service log: `journalctl -u caddy`
- Backend logs: `docker compose -p atlas-main logs -f backend`
- SSH ban state: `sudo fail2ban-client status sshd`

## Rolling back

```bash
ssh deploy@<host> bash -c 'cd /opt/sepsisatlas/main && git fetch && git reset --hard <good-sha> && deploy/deploy-main.sh'
```

## PR preview routing

Each PR is served at `https://atlas.efferon.com/pr/<N>/`. Caddy's `handle_path`
directive strips `/pr/<N>/` before delegating to either the PR's backend
(reverse-proxy to `127.0.0.1:8100+(N%800)`) or its static frontend
(`/var/www/atlas-pr-<N>`). The Astro build for each PR is rooted at
`/pr/<N>/` via `ASTRO_BASE`, and `PUBLIC_BACKEND_URL` is set to `/pr/<N>/api`
so React components target the right backend.

Per-PR snippets live in `/etc/caddy/Caddyfile.d/pr-routes/pr-<N>.caddy` and
are imported inside the main `atlas.efferon.com` site block, so they all
share the single TLS cert and no wildcard DNS is required.

**Known limitation:** assets the FastAPI backend serves under `/static/...`
(e.g. PDF.js vendor files) are shared with the main site, not per-PR. A PR
that adds or modifies files under `/static` will not see those changes in
the preview. Acceptable for previews; if you need full isolation, build a
separate sibling host (e.g. `staging.efferon.com`) and add the A record.

## GitHub Actions deploy

Workflows SSH to the server as `deploy@<host>` with a key stored in repo secrets:

| Secret             | Value                                                       |
| ------------------ | ----------------------------------------------------------- |
| `DEPLOY_SSH_HOST`  | server public IP                                            |
| `DEPLOY_SSH_USER`  | `deploy`                                                    |
| `DEPLOY_SSH_KEY`   | private key whose public half is in `~deploy/.ssh/authorized_keys` |

Generate with `ssh-keygen -t ed25519 -f atlas-deploy -N ''`, install the `.pub`
on the server, push the private key into the GitHub secret.

The repo is public, so the server clones over HTTPS with no auth — no deploy
key required. If the repo ever goes private, generate a deploy key on the server
(`ssh-keygen -t ed25519 -f ~deploy/.ssh/id_ed25519 -N ''`) and install the
public half via `gh repo deploy-key add` (requires admin on the repo).

## Common failures

- **HTTPS won't issue**: `journalctl -u caddy | grep -i error`. Usually DNS doesn't point at the server or port 80 is blocked.
- **Backend 502 at /query**: `docker compose -p atlas-main logs --tail=200 backend`. Common cause: missing or invalid `OPENROUTER_API_KEY`.
- **PR preview port collision**: if two PRs share `8100 + (PR % 800)` (only happens at >800 unique PRs), bump the modulus in `deploy-pr.sh`.
- **`bun: command not found` during deploy**: `/opt/bun/bin/bun` symlink to `/usr/local/bin/bun` may be missing; re-run the `install_bun` section of `bootstrap.sh`.
