#!/usr/bin/env bash
# Deploy a PR preview under atlas.efferon.com/pr/<N>/.
# Usage: deploy-pr.sh <pr_number> <git_sha>
#
# Path-based (no wildcard DNS): the Astro build is rooted at /pr/<N>/, the
# backend is reachable at /pr/<N>/api/* via Caddy reverse-proxy with prefix
# stripping. Backend port is 8100 + (PR % 800) on loopback.

set -euo pipefail

PR="${1:?pr number}"
SHA="${2:?git sha}"

REPO_URL="https://github.com/OrFrederick/SepsisAtlas.git"
WORK_DIR="/opt/sepsisatlas/pr-$PR"
WEB_OUT="/var/www/atlas-pr-$PR"
PROJECT="atlas-pr-$PR"
BASE_PATH="/pr/$PR/"
API_BASE="/pr/$PR/api"
PORT="$((8100 + PR % 800))"

log() { echo "[deploy-pr#$PR $(date -u +%FT%TZ)] $*"; }

if [[ ! -d "$WORK_DIR/.git" ]]; then
  log "cloning into $WORK_DIR"
  git clone "$REPO_URL" "$WORK_DIR"
fi
cd "$WORK_DIR"
git fetch --prune origin "+refs/pull/$PR/head:refs/remotes/origin/pr/$PR"
git reset --hard "$SHA"

# Inherit OPENROUTER_API_KEY etc. from main env unless PR has its own.
[[ -f "$WORK_DIR/.env" ]] || cp /opt/sepsisatlas/main/.env "$WORK_DIR/.env"

log "building frontend with base=$BASE_PATH PUBLIC_BACKEND_URL=$API_BASE"
cd "$WORK_DIR/web"
bun install --frozen-lockfile
ASTRO_BASE="$BASE_PATH" PUBLIC_BACKEND_URL="$API_BASE" bun run build
sudo install -d -o caddy -g caddy "$WEB_OUT"
sudo rsync -a --delete "$WORK_DIR/web/dist/" "$WEB_OUT/"
sudo chown -R caddy:caddy "$WEB_OUT"

log "starting backend stack on 127.0.0.1:$PORT"
cd "$WORK_DIR"
BACKEND_HOST_PORT="$PORT" docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml \
  -p "$PROJECT" up -d --build --remove-orphans

log "waiting for backend health"
for i in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null; then
    log "backend healthy after ${i}s"
    break
  fi
  sleep 1
done

SNIPPET="/etc/caddy/Caddyfile.d/pr-routes/pr-$PR.caddy"
SNIPPET_BODY=$(cat <<EOF
# PR #$PR preview — path-prefixed routes inside the main atlas.efferon.com block.

handle_path /pr/$PR/api/* {
	reverse_proxy 127.0.0.1:$PORT
}

handle_path /pr/$PR/* {
	root * $WEB_OUT
	try_files {path} /index.html
	file_server
}
EOF
)
echo "$SNIPPET_BODY" | sudo tee "$SNIPPET" >/dev/null
sudo systemctl reload caddy
log "deployed: https://atlas.efferon.com/pr/$PR/ (loopback port $PORT)"
