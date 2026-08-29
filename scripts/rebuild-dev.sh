#!/usr/bin/env bash
# Rebuild the dev-configured search image and bring the app up for local testing.
set -euo pipefail

# Round‑13: BuildKit cache mounts in the Dockerfile require the
# BuildKit builder. Toggle both env vars before the build so
# `pip` and `apt` caches survive between runs (cuts a source‑only
# rebuild from ~4 min to ~30s).
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT_DIR/docker-compose.yml" -f "$ROOT_DIR/docker-compose.override.yml")

if command -v sg >/dev/null 2>&1; then
  sg docker -c "cd '$ROOT_DIR' && ${COMPOSE[*]} up -d --build --wait search"
else
  (cd "$ROOT_DIR" && "${COMPOSE[@]}" up -d --build --wait search)
fi

printf '\nDev app: http://localhost:8001\n'
if curl -fsS --max-time 10 http://127.0.0.1:8001/healthz >/dev/null; then
  echo 'Health: OK'
else
  echo 'Health: unavailable' >&2
  exit 1
fi
