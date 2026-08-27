"""Round‑21: one‑off thumbnail generator for the real photos.
Same as /tmp/generate_thumbnails.py on the host. Run inside the
search container where /app/data is writable.
"""

from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

THUMBNAIL_DIR = Path(os.environ.get("THUMBNAIL_DIR", "/app/data/thumbnails"))
THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)

THUMB_MAX_EDGE = int(os.environ.get("THUMB_MAX_EDGE", "256"))
THUMB_QUALITY = int(os.environ.get("THUMB_QUALITY", "78"))

DB_PATH = os.environ.get("DB_PATH", "/app/data/images.db")


def _generate_one(point_id: str, photo_path: str):
    prefix = point_id[:2]
    out_dir = THUMBNAIL_DIR / prefix
    out_path = out_dir / f"{point_id}.webp"
    if out_path.exists():
        return (point_id, True, "exists")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        with Image.open(photo_path) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE), Image.Resampling.LANCZOS)
            im.save(out_path, "WEBP", quality=THUMB_QUALITY, method=6)
        return (point_id, True, "ok")
    except Exception as exc:  # noqa: BLE001
        return (point_id, False, f"{type(exc).__name__}: {exc}")


def main():
    db = sqlite3.connect(DB_PATH)
    rows = db.execute(
        "SELECT id, path FROM images WHERE path LIKE '/mnt/%' OR path LIKE '/tmp/%'"
    ).fetchall()
    print(f"thumbnailing {len(rows)} photos (target dir: {THUMBNAIL_DIR})")

    # Round‑21: rewrite paths from the host's mount (`/mnt/nas-main/...`,
    # `/tmp/...`) to whatever the search container actually sees.
    # The compose mounts the NAS at /nas (NAS_IMAGES_BASE=/nas) and
    # PATH_PREFIX=/mnt/nas-main/misc/data, so a host path of
    # `/mnt/nas-main/misc/data/foo.JPG` resolves to `/nas/foo.JPG`
    # inside the container.
    host_prefix = os.environ.get("HOST_PATH_PREFIX", "/mnt/nas-main/misc/data")
    container_nas = os.environ.get("CONTAINER_NAS", "/nas")
    host_tmp = os.environ.get("HOST_TMP", "/tmp")
    container_tmp = os.environ.get("CONTAINER_TMP", "/tmp")

    workers = int(os.environ.get("THUMB_WORKERS", "8"))
    ok = fail = skip = 0
    fails = []

    def _translate(path: str) -> str:
        """Map a host path to its container-side path."""
        if path.startswith(host_prefix):
            return container_nas + "/" + path[len(host_prefix):].lstrip("/")
        if path.startswith(host_tmp + "/"):
            return container_tmp + path[len(host_tmp):]
        return path

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(_generate_one, pid, _translate(p)): pid
            for pid, p in rows
        }
        for fut in as_completed(futs):
            pid, success, reason = fut.result()
            if not success:
                fail += 1
                fails.append((pid, reason))
            elif reason == "exists":
                skip += 1
            else:
                ok += 1
            if (ok + fail + skip) % 200 == 0:
                print(f"  ... {ok + fail + skip}/{len(rows)} (ok={ok}, skip={skip}, fail={fail})", flush=True)
    print(f"done. ok={ok}, skipped(existing)={skip}, fail={fail}")
    for pid, reason in fails[:5]:
        print(f"  fail {pid[:8]}: {reason}")


if __name__ == "__main__":
    main()