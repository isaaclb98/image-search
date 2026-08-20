#!/usr/bin/env bash
# Run the full OpenAPI → types + zod pipeline from the frontend dir.
# Requires the python venv at the repo root (.venv-test/bin/python).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
"$REPO/.venv-test/bin/python" "$REPO/scripts/dump-openapi.py"
cd "$HERE/.."
node scripts/gen-types.mjs
node scripts/gen-zod.mjs
echo "done"
