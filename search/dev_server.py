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
        default=5,
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
        ("Mountain lake", (57, 105, 168), (235, 190, 92)),
        ("Red fox", (177, 76, 48), (244, 197, 112)),
        ("City at night", (35, 45, 88), (228, 176, 62)),
        ("Tropical beach", (53, 158, 157), (246, 219, 132)),
        ("Forest cabin", (48, 104, 70), (202, 153, 83)),
    ]
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index in range(count):
        label, base, accent = subjects[index % len(subjects)]
        image = Image.new("RGB", (960, 640), base)
        draw = ImageDraw.Draw(image)
        # Simple poster-like geometry makes the demo useful for visual QA.
        draw.rectangle((0, 390, 960, 640), fill=accent)
        draw.ellipse((650, 70, 790, 210), fill=(255, 236, 164))
        draw.polygon([(80, 430), (330, 150), (570, 430)], fill=(35, 65, 90))
        draw.polygon([(360, 430), (610, 180), (900, 430)], fill=(48, 84, 108))
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
        points.append(
            qmodels.PointStruct(
                id=f"00000000-0000-4000-8000-{index + 1:012d}",
                vector=vector,
                payload={
                    "path": str(path),
                    "collection": "demo",
                    "shard": "demo",
                    "width": 960,
                    "height": 640,
                },
            )
        )
    client.upsert(collection_name=collection, points=points)
    qdrant = QdrantSearch(client=client, collection=collection, timeout_ms=2000)
    return create_app(cfg=config.load(), qdrant=qdrant)


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
