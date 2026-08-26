#!/usr/bin/env python3
"""Dump the FastAPI app's OpenAPI schema to frontend/openapi.json.

Single source of truth for the TypeScript types and Zod schemas the
frontend uses. Run from the repo root after backend changes:

    .venv-test/bin/python scripts/dump-openapi.py

The frontend regenerates types from frontend/openapi.json via
`npm run gen:types` and `npm run gen:zod` (in npm scripts). Keep
both in lockstep with the backend — pytest will fail if drift is
detected (see tests/test_openapi_stability.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=str(FRONTEND / "openapi.json"),
        help="Where to write the OpenAPI schema (json).",
    )
    args = parser.parse_args()

    # Minimal config so the app factory doesn't blow up. Real values
    # come from env at deploy time; this is just for spec dumping.
    os.environ.setdefault("SEARCH_TEST_MODE", "1")
    os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
    os.environ.setdefault("MODEL_NAME", "hf-hub:timm/ViT-gopt-16-SigLIP2-384")
    # NAS_IMAGES_BASE is required by config.load() so it can resolve
    # photo paths. The dump doesn't touch the FS — a fake dir is fine.
    fake_nas = Path(os.environ.get("DUMP_NAS", "/tmp/_openapi_dump_nas"))
    fake_nas.mkdir(parents=True, exist_ok=True)
    os.environ["NAS_IMAGES_BASE"] = str(fake_nas)

    sys.path.insert(0, str(REPO_ROOT))
    from search.app import create_app

    app = create_app()
    spec = app.openapi()

    # Sanitise duplicate operationIds. A few routes use
    # `@app.api_route(..., methods=["GET", "HEAD"])` which produces
    # one operationId for both methods, violating the spec. We
    # suffix by HTTP method here so the downstream typescript
    # generator can ingest the file. The backend behaviour is
    # unchanged — operationId is metadata only.
    seen: set[str] = set()
    for _path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for verb, op in methods.items():
            if not isinstance(op, dict):
                continue
            oid = op.get("operationId")
            if not oid:
                continue
            # ensure unique — duplicate IDs come from `api_route(
            # methods=[...])` decorators in the backend.
            candidate = oid
            n = 0
            while candidate in seen:
                n += 1
                candidate = f"{oid}_{n}"
            if candidate != oid:
                op["operationId"] = candidate
            seen.add(candidate)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(spec, indent=2, sort_keys=True))
    print(f"wrote {out_path} ({len(json.dumps(spec)):,} bytes, "
          f"{len(spec.get('paths', {}))} paths, "
          f"{len(spec.get('components', {}).get('schemas', {}))} schemas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
