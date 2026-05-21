#!/usr/bin/env bash
# Deploy the main branch.
# Run as `deploy` user (locally or via SSH from CI).
#
# Backend image lives in ghcr.io and is built by GitHub Actions; this script
# does NOT build it. The compose `pull_policy: always` and the Watchtower
# sidecar pick up new tags. This script:
#   - syncs the repo
#   - builds the frontend (bun)
#   - starts the compose stack (which pulls the latest backend image)
#   - reloads caddy

set -euo pipefail

REPO_URL="https://github.com/OrFrederick/SepsisAtlas.git"
WORK_DIR="/opt/sepsisatlas/main"
WEB_OUT="/var/www/atlas-main"
PROJECT="atlas-main"

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
    log "WARN: $WORK_DIR/.env missing — copying .env.example. Backend will fail until OPENROUTER_API_KEY is set."
    cp "$WORK_DIR/.env.example" "$WORK_DIR/.env"
  fi
}

build_frontend() {
  cd "$WORK_DIR/web"
  log "installing web deps"
  bun install --frozen-lockfile
  log "building web"
  PUBLIC_BACKEND_URL="" bun run build
  sudo install -d -o caddy -g caddy "$WEB_OUT"
  sudo rsync -a --delete "$WORK_DIR/web/dist/" "$WEB_OUT/"
  sudo chown -R caddy:caddy "$WEB_OUT"
}

restart_stack() {
  cd "$WORK_DIR"

  # Pre-flight: confirm the backend image is reachable BEFORE compose tries
  # to recreate the container. Without this guard, a missing image (e.g. the
  # first deploy before build-backend has ever run) would tear down the
  # currently-running container without a replacement.
  local image
  image=$(docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" config | awk '/^[[:space:]]+image:.*sepsis-atlas-backend/{print $2; exit}')
  if [[ -n "$image" ]]; then
    log "pre-flight: docker pull $image"
    if ! docker pull "$image"; then
      log "ERROR: cannot pull $image — leaving stack alone so the old backend keeps running."
      log "Hint: trigger the build first with: gh workflow run \"deploy main\""
      exit 1
    fi
  fi

  log "starting compose stack ($PROJECT)"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" up -d --remove-orphans
}

smoke() {
  log "waiting for backend health on 127.0.0.1:8000"
  for i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:8000/health" >/dev/null; then
      log "backend healthy after ${i}s"
      return 0
    fi
    sleep 1
  done
  log "ERROR: backend did not become healthy"
  cd "$WORK_DIR"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" logs --tail=200 backend || true
  exit 1
}

reload_caddy() {
  # Validate from the source file BEFORE overwriting the live config —
  # otherwise a bad Caddyfile lands on disk and a future reload (e.g. on
  # service restart) brings caddy down.
  caddy validate --config "$WORK_DIR/deploy/Caddyfile" --adapter caddyfile
  sudo cp "$WORK_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
  sudo systemctl reload caddy
}

main() {
  ensure_clone
  pull_main
  ensure_env
  build_frontend
  restart_stack
  smoke
  reload_caddy
  log "main deploy complete"
}

main "$@"
