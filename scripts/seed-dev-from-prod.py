#!/usr/bin/env python3
"""
seed-dev-from-prod.py — seed dev's Qdrant with a sample of prod's photos.

Pulls N photos from prod's `images` collection (including vectors)
and upserts them into dev's `images` collection. Vectors are included
so dev's similarity search keeps working; without them, dev could
list but not search.

Companion to the thumbnail bind-mount added to dev compose —
scripts assume dev's /app/data/thumbnails is prod's pre-computed
thumbnails (read-only). Without the bind, thumbnails 404 even when
metadata is present.

Safety:
  - Dev's Qdrant is at localhost:16333 (separate port + container from
    prod's 6333). Writes go to dev's collection only.
  - Dev's thumbnail bind is :ro. Dev can't overwrite prod's thumbnails.
  - Dev's NAS bind is :ro. Dev can't touch prod's photos.
  - The default --count is small (200). Pass --count higher only if
    you need more; the transfer time scales linearly.

Usage:
    python3 scripts/seed-dev-from-prod.py                  # 200 random photos
    python3 scripts/seed-dev-from-prod.py --count 1000    # 1000 photos
    python3 scripts/seed-dev-from-prod.py --source telegram
    python3 scripts/seed-dev-from-prod.py --source telegram --source collections
    python3 scripts/seed-dev-from-prod.py --list            # show how many
                                                          # would be sampled
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from typing import Any

PROD_URL = "http://127.0.0.1:6333"
DEV_URL = "http://127.0.0.1:16333"
DEV_API_URL = "http://127.0.0.1:18000"
PROD_COLLECTION = "images"
DEV_COLLECTION = "images"
SCROLL_BATCH = 500
# How many prod points to scroll as candidates before sampling.
# 5000 candidates for a 200-photo sample gives reasonable variety
# without paying for a full 1.4M scroll. Uniform-random sampling
# requires either scrolling everything or using a reservoir sample;
# for UI testing, "first N from a 5K candidate pool" is close enough.
SAMPLE_POOL = 5000


def _http(url: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _scroll_collection(url: str, collection: str, sources: list[str],
                      with_vectors: bool = True, max_points: int | None = None
                      ) -> list[dict]:
    """Scroll points in `collection`, filtered by source if given.
    Qdrant's scroll endpoint paginates; we walk pages up to
    `max_points` if given, else walk all of them.

    For UI sampling we set max_points to a small candidate pool
    (default 5000) so a 200-photo sample doesn't pay for a full
    1.4M scroll over localhost. The first N from a filtered walk
    aren't strictly uniform-random across the whole collection,
    but they're diverse enough for visual testing."""
    out: list[dict] = []
    offset: Any = None
    while True:
        if max_points is not None and len(out) >= max_points:
            out = out[:max_points]
            break
        body: dict[str, Any] = {
            "limit": SCROLL_BATCH,
            "with_payload": True,
            "with_vectors": with_vectors,
        }
        if sources:
            body["filter"] = {
                "should": [
                    {"key": "collection", "match": {"value": s}}
                    for s in sources
                ],
                "min_count": 1,
            }
        if offset is not None:
            body["offset"] = offset
        d = _http(f"{url}/collections/{collection}/points/scroll",
                  method="POST", body=body)
        out.extend(d["result"]["points"])
        offset = d["result"].get("next_page_offset")
        if offset is None:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=200,
                    help="How many prod photos to seed dev with (default 200)")
    ap.add_argument("--source", action="append",
                    choices=["telegram", "collections", "data"],
                    help="Filter prod by source collection (repeatable)")
    ap.add_argument("--seed", type=int, default=None,
                    help="Random seed for reproducibility")
    ap.add_argument("--list", action="store_true",
                    help="Show counts and exit; don't seed")
    ap.add_argument("--wipe", action="store_true",
                    help="Delete dev's collection before seeding (clean slate)")
    args = ap.parse_args()

    sources = args.source or ["telegram", "collections", "data"]
    rng = random.Random(args.seed)

    # Show how many would be sampled
    prod_total = _http(f"{PROD_URL}/collections/{PROD_COLLECTION}")["result"]["points_count"]
    print(f"prod: {prod_total:,} photos in '{PROD_COLLECTION}'")
    if args.source:
        src_total = 0
        for s in args.source:
            n = _http(f"{PROD_URL}/collections/{PROD_COLLECTION}/points/count",
                     method="POST",
                     body={"filter": {"must": [{"key": "collection", "match": {"value": s}}]},
                           "exact": True})["result"]["count"]
            print(f"  source={s}: {n:,}")
            src_total += n
        effective_total = src_total
    else:
        effective_total = prod_total
    print(f"sampling {args.count} of {effective_total:,}")

    if args.list:
        return 0

    # Scroll a candidate pool (default 5000) — much faster than walking
    # all 1.4M prod points just to sample 200. With SAMPLE_POOL=5000
    # this takes ~3s over localhost; with the full 1.4M it took 60s+.
    print(f"scrolling prod ({PROD_COLLECTION}), pool={SAMPLE_POOL}...")
    t0 = time.monotonic()
    pool = _scroll_collection(PROD_URL, PROD_COLLECTION, args.source,
                              with_vectors=True, max_points=SAMPLE_POOL)
    print(f"  scrolled {len(pool):,} candidate points in {time.monotonic()-t0:.1f}s")

    # Random sample down to --count
    if len(pool) > args.count:
        sample = rng.sample(pool, args.count)
    else:
        sample = pool
    print(f"sampled {len(sample):,} photos from pool of {len(pool):,}")

    # Optionally wipe dev's collection for a clean seed
    if args.wipe:
        print(f"deleting dev collection '{DEV_COLLECTION}'...")
        try:
            _http(f"{DEV_URL}/collections/{DEV_COLLECTION}", method="DELETE")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise

    # Recreate dev collection if missing (after wipe, or if never existed)
    try:
        _http(f"{DEV_URL}/collections/{DEV_COLLECTION}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"creating dev collection '{DEV_COLLECTION}'...")
            # Match prod's vector config so the points land cleanly.
            info = _http(f"{PROD_URL}/collections/{PROD_COLLECTION}")["result"]
            vec_cfg = info["config"]["params"]["vectors"]
            _http(f"{DEV_URL}/collections/{DEV_COLLECTION}",
                  method="PUT",
                  body={"vectors": vec_cfg})
        else:
            raise

    # Upsert in batches. Qdrant's upsert is idempotent on id — re-running
    # the seed refreshes existing points.
    print(f"upserting {len(sample):,} points into dev '{DEV_COLLECTION}'...")
    t0 = time.monotonic()
    upserted = 0
    for i in range(0, len(sample), SCROLL_BATCH):
        batch = sample[i:i + SCROLL_BATCH]
        _http(f"{DEV_URL}/collections/{DEV_COLLECTION}/points",
              method="PUT", body={"points": batch})
        upserted += len(batch)
    print(f"  upserted {upserted:,} points in {time.monotonic()-t0:.1f}s")

    # The dev backend keeps a SQLite IndexDB mirror of Qdrant metadata
    # that backs /api/random and /api/photo/{id}/raw. Without a refresh,
    # the API keeps returning the OLD photos from its cache. Hit
    # POST /api/cache/refresh to force a full rebuild from Qdrant.
    # This walks the dev collection, not prod's, so the refresh
    # scales with our seeded sample size, not the 1.4M prod corpus.
    print("refreshing dev IndexDB cache...")
    try:
        r = _http(f"{DEV_API_URL}/api/cache/refresh", method="POST")
        print(f"  refresh ok: {r.get('count', '?')} points, took {r.get('took_ms', '?')}ms")
    except urllib.error.URLError as e:
        print(f"  refresh skipped (dev API not reachable: {e.reason})")
    except Exception as e:
        print(f"  refresh failed: {e}")

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())