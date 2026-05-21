#!/usr/bin/env bash
# Deploy a PR preview at pr-<N>.atlas.efferon.com.
# Usage: deploy-pr.sh <pr_number> <git_sha>
#
# Picks loopback port 8100 + (PR % 800) so up to ~800 distinct PRs can have
# concurrent previews without colliding.

set -euo pipefail

PR="${1:?pr number}"
SHA="${2:?git sha}"

REPO_URL="git@github.com:OrFrederick/SepsisAtlas.git"
WORK_DIR="/opt/sepsisatlas/pr-$PR"
WEB_OUT="/var/www/atlas-pr-$PR"
PROJECT="atlas-pr-$PR"
HOST="pr-$PR.atlas.efferon.com"
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

log "building frontend"
cd "$WORK_DIR/web"
bun install --frozen-lockfile
PUBLIC_BACKEND_URL="" bun run build
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

SNIPPET="/etc/caddy/Caddyfile.d/pr-$PR.caddy"
SNIPPET_BODY=$(cat <<EOF
$HOST {
	encode zstd gzip
	root * $WEB_OUT
	@api path /query* /viewer/* /papers/* /static/* /health* /phenotypes* /rank_predictors* /ingest_pubmed /query_kg* /kg*
	handle @api {
		reverse_proxy 127.0.0.1:$PORT
	}
	handle {
		try_files {path} /index.html
		file_server
	}
	log {
		output file /var/log/caddy/$HOST.access.log
	}
}
EOF
)
echo "$SNIPPET_BODY" | sudo tee "$SNIPPET" >/dev/null
sudo systemctl reload caddy
log "deployed: https://$HOST (loopback port $PORT)"
