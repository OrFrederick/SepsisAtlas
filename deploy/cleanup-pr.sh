#!/usr/bin/env bash
# Tear down a PR preview.
# Usage: cleanup-pr.sh <pr_number>

set -euo pipefail

PR="${1:?pr number}"
WORK_DIR="/opt/sepsisatlas/pr-$PR"
WEB_OUT="/var/www/atlas-pr-$PR"
PROJECT="atlas-pr-$PR"
SNIPPET="/etc/caddy/Caddyfile.d/pr-routes/pr-$PR.caddy"

if [[ -d "$WORK_DIR" ]]; then
  cd "$WORK_DIR"
  docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" down -v --remove-orphans || true
fi

sudo rm -f "$SNIPPET"
sudo systemctl reload caddy || true
sudo rm -rf "$WEB_OUT"
rm -rf "$WORK_DIR"

echo "[cleanup-pr#$PR] removed"
