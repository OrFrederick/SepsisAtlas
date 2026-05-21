#!/usr/bin/env bash
# Deploy the main branch.
# Run as `deploy` user (locally or via SSH from CI).

set -euo pipefail

REPO_URL="git@github.com:OrFrederick/SepsisAtlas.git"
WORK_DIR="/opt/sepsisatlas/main"
WEB_OUT="/var/www/atlas-main"
PROJECT="atlas-main"
BACKEND_HOST_PORT="8000"

log() { echo "[deploy-main $(date -u +%FT%TZ)] $*"; }

ensure_clone() {
  if [[ ! -d "$WORK_DIR/.git" ]]; then
    log "cloning $REPO_URL → $WORK_DIR"
    git clone --branch main "$REPO_URL" "$WORK_DIR"
  fi
}

pull_main() {
  cd "$WORK_DIR"
  git fetch --prune origin main
  git reset --hard origin/main
}

ensure_env() {
  if [[ ! -f "$WORK_DIR/.env" ]]; then
    log "WARN: $WORK_DIR/.env missing — copying .env.example. Backend extraction will fail until OPENROUTER_API_KEY is set."
    cp "$WORK_DIR/.env.example" "$WORK_DIR/.env"
  fi
}

build_frontend() {
  cd "$WORK_DIR/web"
  log "installing web deps"
  bun install --frozen-lockfile
  log "building web (PUBLIC_BACKEND_URL='' → same-origin)"
  PUBLIC_BACKEND_URL="" bun run build
  sudo install -d -o caddy -g caddy "$WEB_OUT"
  sudo rsync -a --delete "$WORK_DIR/web/dist/" "$WEB_OUT/"
  sudo chown -R caddy:caddy "$WEB_OUT"
}

build_backend() {
  cd "$WORK_DIR"
  log "building backend image"
  BACKEND_HOST_PORT="$BACKEND_HOST_PORT" docker compose \
    -f docker-compose.yml -f docker-compose.prod.yml \
    -p "$PROJECT" build
}

restart_stack() {
  cd "$WORK_DIR"
  log "starting compose stack ($PROJECT) on 127.0.0.1:$BACKEND_HOST_PORT"
  BACKEND_HOST_PORT="$BACKEND_HOST_PORT" docker compose \
    -f docker-compose.yml -f docker-compose.prod.yml \
    -p "$PROJECT" up -d --remove-orphans
}

smoke() {
  log "waiting for backend health on 127.0.0.1:$BACKEND_HOST_PORT"
  for i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$BACKEND_HOST_PORT/health" >/dev/null; then
      log "backend healthy after ${i}s"
      return 0
    fi
    sleep 1
  done
  log "ERROR: backend did not become healthy"
  docker compose -p "$PROJECT" logs --tail=200 backend || true
  exit 1
}

reload_caddy() {
  sudo cp "$WORK_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  sudo systemctl reload caddy
}

main() {
  ensure_clone
  pull_main
  ensure_env
  build_frontend
  build_backend
  restart_stack
  smoke
  reload_caddy
  log "main deploy complete"
}

main "$@"
