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
curl -fsSL "$RAW/update-compose.sh"       -o update-compose.sh
chmod +x update-compose.sh

docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml -p "$PROJECT" up -d --remove-orphans
