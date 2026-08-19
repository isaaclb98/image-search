"""Fast local development server with optional model-free demo data."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run image-search locally for UI iteration."
    )
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="Use the deterministic mock text encoder; do not load/download SigLIP2.",
    )
    parser.add_argument(
        "--demo-data",
        action="store_true",
        help="Start with an in-memory Qdrant collection and N demo photos (--demo-count).",
    )
    parser.add_argument(
        "--demo-count",
        type=int,
        default=200,
        metavar="N",
        help="Number of generated demo photos (default: 200 — enough to exercise infinite scroll).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    return parser


def _make_demo_images(root: Path, count: int) -> list[Path]:
    """Generate synthetic demo photos using scripts/synth_photos.py.

    Earlier versions of this function wrote flat 2-tone PNGs with
    a single horizon line. The synth_photos generator produces
    realistic-ish JPEGs with sky gradients, mountain silhouettes,
    city skylines, etc. — much closer to what the UI is meant to
    render. The seeded RNG ensures the same demo set every run,
    so screenshots are stable.
    """
    import sys as _sys
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from scripts.synth_photos import generate as synth_generate  # type: ignore[import-not-found]

    root.mkdir(parents=True, exist_ok=True)
    result = synth_generate(root, count=count, prefix="demo", seed=42)
    return [Path(item["path"]) for item in result.index]


def _build_demo_app(count: int):
    """Build the real app against an in-memory Qdrant with local demo photos."""
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels

    from search import config
    from search.app import create_app
    from search.qdrant_client import QdrantSearch

    os.environ["SEARCH_NO_MODEL"] = "1"
    os.environ["SEARCH_TEST_MODE"] = "1"
    os.environ["NAS_IMAGES_BASE"] = str(Path(tempfile.gettempdir()) / "image-search-demo")
    os.environ["INDEX_DB_PATH"] = str(Path(tempfile.gettempdir()) / "image-search-demo.db")
    os.environ["QDRANT_COLLECTION"] = "images_demo"
    # API result URLs must point at this local server, not the production
    # default (localhost:8000), otherwise Chromium shows empty cards.
    os.environ.setdefault("WEB_UI_URL", "http://127.0.0.1:8765")

    demo_root = Path(os.environ["NAS_IMAGES_BASE"])
    paths = _make_demo_images(demo_root, count)
    client = QdrantClient(location=":memory:")
    collection = "images_demo"
    client.create_collection(
        collection_name=collection,
        vectors_config=qmodels.VectorParams(size=1536, distance=qmodels.Distance.COSINE),
    )
    points = []
    for index, path in enumerate(paths):
        vector = [0.0] * 1536
        vector[index] = 1.0
        # Read image dimensions; use pre-computed blurhash for speed.
        from PIL import Image as _Img
        _img = _Img.open(path)
        _sz = _img.size
        # Deterministic blurhashes keyed by index — avoids 6s/image encoding.
        _bhs = [
            "LGF%xQ%LNK^j~WNGaaay0gM{RP", "LDA]Rj-RS5Rj00%MRjRj~WWBt7",
            "LHF$p5WBIUxu~WIUbbaxDgM{WB", "LGDJz[D%WBxu%%WAt7xuDgM{WB",
            "LKFRbDISxu~pIUt7RjayDgM{WB", "LJF#7cRjD%kW?bRjRjayDgM{WB",
            "LIE8KkRjD%kW?bRj~pRjDgM{WB", "LHE-X~RjD%kW?bRj~pRjDgM{WB",
            "LEF~_9RjD%kW?bRj~pRjDgM{WB", "LDFzKrRjD%kW?bRj~pRjDgM{WB",
            "LCF5LvRjD%kW?bRj~pRjDgM{WB", "LBFyLwRjD%kW?bRj~pRjDgM{WB",
            "LAFOJxRjD%kW?bRj~pRjDgM{WB", "K9F~KzRjD%kW?bRj~pRjDgM{WB",
            "K8GeK0RjD%kW?bRj~pRjDgM{WB", "K7FdL1RjD%kW?bRj~pRjDgM{WB",
            "K6EeM2RjD%kW?bRj~pRjDgM{WB", "K5DfN3RjD%kW?bRj~pRjDgM{WB",
            "K4CgO4RjD%kW?bRj~pRjDgM{WB", "K3BhP5RjD%kW?bRj~pRjDgM{WB}",
        ]
        bh = _bhs[index % len(_bhs)]
        points.append(
            qmodels.PointStruct(
                id=f"00000000-0000-4000-8000-{index + 1:012d}",
                vector=vector,
                payload={
                    "path": str(path),
                    "collection": "demo",
                    "shard": "demo",
                    "width": _sz[0],
                    "height": _sz[1],
                    **(({"blurhash": bh}) if bh else {}),
                },
            )
        )
    client.upsert(collection_name=collection, points=points)
    qdrant = QdrantSearch(client=client, collection=collection, timeout_ms=2000)
    app = create_app(cfg=config.load(), qdrant=qdrant)

    # ── Seed relational data (after app init creates tables) ────
    import sqlite3 as _sqlite3
    _db_path = os.environ["INDEX_DB_PATH"]
    with _sqlite3.connect(_db_path) as conn:
        fav_count = max(1, len(points) // 2)
        for i in range(fav_count):
            conn.execute(
                "INSERT OR IGNORE INTO favorites (id, favorited_at) VALUES (?, datetime('now'))",
                (points[i].id,),
            )
        for i in range(len(points) - 3, len(points)):
            conn.execute(
                "INSERT OR IGNORE INTO dislikes (id, disliked_at, source) VALUES (?, datetime('now'), 'manual')",
                (points[i].id,),
            )
        cur = conn.execute(
            "INSERT INTO albums (name, description, created_at, updated_at) VALUES (?, ?, datetime('now'), datetime('now'))",
            ("Landscape picks", "Curated landscape photos from the library."),
        )
        a1 = cur.lastrowid
        for i in range(min(3, fav_count)):
            conn.execute(
                "INSERT OR IGNORE INTO album_memberships (album_id, favorite_id, added_at) VALUES (?, ?, datetime('now'))",
                (a1, points[i].id),
            )
        cur2 = conn.execute(
            "INSERT INTO albums (name, description, created_at, updated_at) VALUES (?, ?, datetime('now'), datetime('now'))",
            ("Night scenes", "Urban and night photography."),
        )
        a2 = cur2.lastrowid
        for i in range(min(6, fav_count), min(9, len(points))):
            conn.execute(
                "INSERT OR IGNORE INTO album_memberships (album_id, favorite_id, added_at) VALUES (?, ?, datetime('now'))",
                (a2, points[i].id),
            )
    return app


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.no_model or args.demo_data:
        os.environ["SEARCH_NO_MODEL"] = "1"

    import uvicorn

    if args.demo_data:
        app = _build_demo_app(args.demo_count)
        uvicorn.run(app, host=args.host, port=args.port)
    else:
        uvicorn.run(
            "search.app:_build_default_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
        )


if __name__ == "__main__":
    main()
