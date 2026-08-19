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
        help="Start with a tiny in-memory Qdrant collection and five demo photos.",
    )
    parser.add_argument(
        "--demo-count",
        type=int,
        default=10,
        choices=range(1, 21),
        metavar="N",
        help="Number of generated demo photos (default: 5).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    return parser


def _make_demo_images(root: Path, count: int) -> list[Path]:
    from PIL import Image, ImageDraw

    subjects = [
        ("Mountain lake", (57, 105, 168), (235, 190, 92), 960, 640),
        ("Red fox", (177, 76, 48), (244, 197, 112), 800, 800),
        ("City at night", (35, 45, 88), (228, 176, 62), 1200, 800),
        ("Tropical beach", (53, 158, 157), (246, 219, 132), 640, 960),
        ("Forest cabin", (48, 104, 70), (202, 153, 83), 960, 960),
        ("Ocean sunset", (22, 65, 120), (245, 160, 70), 960, 640),
        ("Desert dunes", (194, 154, 108), (232, 210, 170), 800, 600),
        ("Snow mountain", (180, 195, 210), (255, 255, 255), 1200, 800),
        ("Autumn forest", (180, 80, 30), (240, 180, 60), 960, 640),
        ("City skyline", (25, 30, 60), (180, 140, 80), 1400, 700),
        ("Garden flowers", (40, 120, 50), (220, 80, 120), 800, 800),
        ("Misty hills", (100, 120, 140), (200, 210, 220), 960, 640),
        ("River valley", (60, 130, 110), (180, 200, 140), 1000, 660),
        ("Night sky", (10, 10, 40), (200, 200, 255), 640, 960),
        ("Autumn leaves", (160, 70, 20), (240, 200, 80), 960, 960),
        ("Winter cabin", (70, 85, 110), (240, 230, 220), 800, 600),
        ("Coral reef", (20, 100, 120), (255, 150, 100), 960, 640),
        ("Lavender field", (120, 80, 160), (240, 220, 255), 1200, 800),
        ("Volcanic landscape", (80, 30, 20), (200, 100, 50), 960, 640),
        ("Waterfall", (40, 100, 120), (220, 230, 240), 800, 1000),
    ]
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index in range(count):
        label, base, accent, w, h = subjects[index % len(subjects)]
        image = Image.new("RGB", (w, h), base)
        draw = ImageDraw.Draw(image)
        # Simple poster-like geometry makes the demo useful for visual QA.
        horizon = h * 6 // 10
        draw.rectangle((0, horizon, w, h), fill=accent)
        # Sun
        sun_x = w * 3 // 4 + (index * 47) % (w // 5)
        sun_y = horizon // 3 + (index * 31) % (horizon // 3)
        sun_r = min(w, h) // 10
        draw.ellipse((sun_x - sun_r, sun_y - sun_r, sun_x + sun_r, sun_y + sun_r), fill=(255, 236, 164))
        # Mountains
        import math
        pts = []
        for x in range(0, w + 1, 50):
            y = horizon - 10 - int(50 * abs(math.sin((x + index * 97) / 180)))
            pts.append((x, y))
        pts += [(w, horizon), (0, horizon)]
        draw.polygon(pts, fill=tuple(max(0, c - 20) for c in base))
        draw.text((42, 42), label, fill=(255, 255, 255))
        path = root / f"demo_{index + 1:02d}.jpg"
        image.save(path, quality=90)
        paths.append(path)
    return paths


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
