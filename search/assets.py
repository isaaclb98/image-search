"""
search/assets.py — content-hashed static asset URL helper.

At startup we walk the static directory and compute a short SHA256
hash for each file. Templates use ``{{ asset('js/app.js') }}`` and
get back ``/static/js/app.<hash>.js``. The middleware treats paths
matching ``<name>.<8-hex>.<ext>`` as immutable.

Why:
- ``Cache-Control: public, max-age=31536000, immutable`` is safe only
  when the URL itself changes when bytes change. Adding the hash to
  the filename gives us that for free.
- Eliminates the old ``?v=N`` query string and the dev-only
  ``no-cache, must-revalidate`` blanket that was hurting prod.

Developers edit a file → hash changes → URL changes → browser
re-fetches. No build step, no manifest, just a per-file hash.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Match ``/static/<rest>.<8-hex>.<ext>``. Captures the
# "logical" path (everything before the hash) so we can serve the
# underlying file unchanged. <rest> may contain '/' (subdir) or be a
# bare filename (root-level files like favicon).
_HASHED_PATH_RE = re.compile(
    r"^/static/(?P<rest>.+?)\.[0-9a-f]{8}\.(?P<ext>[a-z0-9]+)$"
)


class AssetManifest:
    """In-memory map of logical-asset-path → cached/public URL."""

    def __init__(self) -> None:
        # logical path (e.g. "js/app.js") -> public URL ("/static/js/app.<hash>.js")
        self._urls: dict[str, str] = {}

    def build(self, static_dir: Path) -> None:
        static_dir = Path(static_dir)
        if not static_dir.is_dir():
            logger.warning("asset manifest skipped: %s is not a directory", static_dir)
            return
        for path in sorted(static_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(static_dir).as_posix()
            h = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
            parent = path.parent.relative_to(static_dir).as_posix()
            stem = path.stem
            ext = path.suffix.lstrip(".")
            if parent == ".":
                hashed_name = f"{stem}.{h}.{ext}"
            else:
                hashed_name = f"{parent}/{stem}.{h}.{ext}"
            self._urls[rel] = f"/static/{hashed_name}"
        logger.info(
            "asset manifest built: %d files, sample=%s",
            len(self._urls),
            next(iter(self._urls.values()), "(empty)"),
        )

    def url(self, logical_path: str) -> str:
        """Public URL for a logical asset path (e.g. ``js/app.js``).

        Returns the original path with no hash suffix when the file
        is not in the manifest — dev-friendly fallback so a missing
        file doesn't blow up the template. Caller should still log
        or assert in dev.
        """
        if not logical_path:
            return "/static/"
        if logical_path in self._urls:
            return self._urls[logical_path]
        return f"/static/{logical_path.lstrip('/')}"

    def lookup(self, request_path: str) -> str | None:
        """If ``request_path`` looks hashed, return the logical path
        (so the StaticFiles mount serves the real file). Otherwise None.
        """
        m = _HASHED_PATH_RE.match(request_path)
        if m:
            return f"/static/{m['rest']}.{m['ext']}"
        return None


# Singleton, populated by ``init()`` once at startup.
MANIFEST = AssetManifest()


def init(static_dir: Path | None = None) -> AssetManifest:
    """Build the manifest and return it.

    On first call, ``static_dir`` is auto-resolved to the package's
    own ``static/`` dir if not given. Subsequent calls rebuild.
    """
    if static_dir is None:
        static_dir = Path(__file__).parent / "static"
    MANIFEST.build(static_dir)
    return MANIFEST


# Build once at import time so any caller (tests, scripts, the
# app) sees a populated manifest without an explicit init call.
init()


def install_jinja(env) -> None:
    """Register ``asset()`` as a Jinja global."""
    env.globals["asset"] = MANIFEST.url


def is_hashed_path(request_path: str) -> bool:
    return _HASHED_PATH_RE.match(request_path) is not None


class HashedStaticApp:
    """ASGI wrapper around ``StaticFiles`` that resolves content-hashed
    URLs to their underlying file path before delegating.

    Request:  GET /static/js/app.abc123def0.js
    Resolves:  /static/js/app.js
    (Starlette ``StaticFiles`` then serves the real file with its own
    ETag/304 handling.)

    Keeps the source ``static/`` directory free of build artifacts —
    no sibling ``app.abc123def0.js`` files written to disk.
    """

    def __init__(self, app, manifest: AssetManifest) -> None:
        self.app = app
        self.manifest = manifest

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/static/"):
            logical = self.manifest.lookup(scope["path"])
            if logical:
                scope = {**scope, "path": logical}
        return await self.app(scope, receive, send)
