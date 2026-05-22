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

# Self-update via temp + mv. Curling directly into update-compose.sh would
# overwrite the file bash is still reading (bash reads scripts in blocks,
# not all-at-once), which silently truncates execution if the new version
# is shorter. Atomic rename avoids that — bash keeps the original inode
# until exit; the new copy takes effect on the NEXT run.
curl -fsSL "$RAW/update-compose.sh" -o update-compose.sh.new
chmod +x update-compose.sh.new
mv update-compose.sh.new update-compose.sh

docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" up -d --remove-orphans
