#!/usr/bin/env bash
# scripts/seed-synth-data.sh — generate synthetic photos and index
# them into the dev Qdrant.
#
# This is the "real synthetic data" pipeline Isaac asked for:
#   - 80+ photos across mountain / beach / forest / desert / city /
#     wildlife / portrait / still life / abstract subjects
#   - real JPEG output with layered silhouettes + sky gradients
#   - deterministic seed so screenshots are reproducible
#   - indexed via the existing local_sync.py so the search side
#     sees real Qdrant points with vectors + blurhash payloads
#
# Usage:
#   scripts/dev-qdrant.sh up            # bring up Qdrant
#   scripts/seed-synth-data.sh          # default: 80 photos, /tmp/is-synth
#   scripts/seed-synth-data.sh --count 200 --out /tmp/big-synth
#
# Then point the dev backend at it:
#   NAS_IMAGES_BASE=/tmp/is-synth \
#   QDRANT_URL=http://localhost:6333 \
#   .venv-test/bin/python -m search.dev_server --no-model --host 127.0.0.1 --port 8765
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
VENV="$REPO/.venv-test"

OUT_DIR="/tmp/is-synth"
COUNT=80
SEED=42

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)  OUT_DIR="$2"; shift 2 ;;
    --count) COUNT="$2"; shift 2 ;;
    --seed) SEED="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 64 ;;
  esac
done

echo "▶ ensuring dev qdrant is up…"
"$HERE/dev-qdrant.sh" up >/dev/null

echo "▶ generating $COUNT synthetic photos into $OUT_DIR…"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
"$VENV/bin/python" "$REPO/scripts/synth_photos.py" \
  --out "$OUT_DIR" \
  --count "$COUNT" \
  --prefix demo \
  --seed "$SEED"

echo "▶ indexing photos into dev qdrant (this loads the SigLIP2 model)…"
SEARCH_NO_MODEL=0 SEARCH_TEST_MODE=0 QDRANT_URL=http://localhost:6333 \
  QDRANT_COLLECTION=images_dev \
  MODEL_NAME=hf-hub:timm/ViT-gopt-16-SigLIP2-384 \
  NAS_IMAGES_BASE="$OUT_DIR" \
  INDEX_DB_PATH="$OUT_DIR/index.db" \
  "$VENV/bin/python" -m indexer.local_sync \
    --qdrant-url http://localhost:6333 \
    --collection images_dev \
    --photos-root "$OUT_DIR" \
    --incremental 2>&1 | tail -20

echo
echo "✓ seed complete"
echo "  photos:       $OUT_DIR"
echo "  qdrant:       http://localhost:6333 (collection: images_dev)"
echo "  points total: $(curl -fs 'http://localhost:6333/collections/images_dev' | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["points_count"])')"
echo "  next:         run the dev backend with QDRANT_URL=http://localhost:6333"
echo "                NAS_IMAGES_BASE=$OUT_DIR and you have a real seeded library."
