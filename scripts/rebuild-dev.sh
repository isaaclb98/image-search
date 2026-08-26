#!/usr/bin/env bash
# Rebuild the dev-configured search image and bring the app up for local testing.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT_DIR/docker-compose.yml" -f "$ROOT_DIR/docker-compose.override.yml")

if command -v sg >/dev/null 2>&1; then
  sg docker -c "cd '$ROOT_DIR' && ${COMPOSE[*]} up -d --build --wait search"
else
  (cd "$ROOT_DIR" && "${COMPOSE[@]}" up -d --build --wait search)
fi

printf '\nDev app: http://localhost:8000\n'
if curl -fsS --max-time 10 http://127.0.0.1:8000/healthz >/dev/null; then
  echo 'Health: OK'
else
  echo 'Health: unavailable' >&2
  exit 1
fi
