#!/usr/bin/env bash
# scripts/dev-qdrant.sh — start a local Qdrant container for dev/test.
#
# Uses docker compose to bring up Qdrant on :6333 with a persistent
# volume. This is the "dev-only test Qdrant" you point your local
# search / frontend / indexer at via QDRANT_URL=http://localhost:6333.
#
# The docker-compose qdrant service is already declared in
# docker/docker-compose.yml with a healthcheck, so this is just
# glue — `docker compose up qdrant` in the right place.
#
# Usage:
#   scripts/dev-qdrant.sh up         # start (idempotent)
#   scripts/dev-qdrant.sh down       # stop, keep volume
#   scripts/dev-qdrant.sh reset      # nuke and restart (deletes data)
#   scripts/dev-qdrant.sh status     # print health
#   scripts/dev-qdrant.sh logs       # tail logs
#
# After `up`, the seed pipeline (scripts/seed-synth-data.sh) can
# index photos into it.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
COMPOSE_FILE="$REPO/docker/docker-compose.yml"
COMPOSE_CMD=(docker compose -f "$COMPOSE_FILE")
SERVICE="qdrant"

cmd="${1:-up}"

case "$cmd" in
  up)
    if ! docker info >/dev/null 2>&1; then
      echo "ERROR: docker is not reachable. Start docker first." >&2
      exit 1
    fi
    echo "▶ bringing up $SERVICE (persistent volume qdrant_data)…"
    "${COMPOSE_CMD[@]}" up -d "$SERVICE"
    echo "▶ waiting for qdrant to become healthy…"
    for _ in $(seq 1 30); do
      if curl -fs http://localhost:6333/healthz >/dev/null 2>&1; then
        echo "✓ qdrant up at http://localhost:6333"
        exit 0
      fi
      sleep 1
    done
    echo "ERROR: qdrant did not become healthy in 30s" >&2
    exit 1
    ;;
  down)
    echo "▶ stopping $SERVICE…"
    "${COMPOSE_CMD[@]}" stop "$SERVICE"
    echo "✓ stopped (volume preserved; restart with 'up')"
    ;;
  reset)
    echo "▶ resetting $SERVICE (deletes the qdrant_data volume)…"
    "${COMPOSE_CMD[@]}" down -v "$SERVICE"
    "${COMPOSE_CMD[@]}" up -d "$SERVICE"
    echo "▶ waiting for qdrant to become healthy…"
    for _ in $(seq 1 30); do
      if curl -fs http://localhost:6333/healthz >/dev/null 2>&1; then
        echo "✓ qdrant reset at http://localhost:6333"
        exit 0
      fi
      sleep 1
    done
    echo "ERROR: qdrant did not become healthy in 30s" >&2
    exit 1
    ;;
  status)
    if curl -fs http://localhost:6333/healthz >/dev/null 2>&1; then
      echo "✓ qdrant healthy at http://localhost:6333"
    else
      echo "✗ qdrant not reachable"
      exit 1
    fi
    ;;
  logs)
    "${COMPOSE_CMD[@]}" logs -f "$SERVICE"
    ;;
  *)
    echo "Usage: $0 {up|down|reset|status|logs}" >&2
    exit 64
    ;;
esac
