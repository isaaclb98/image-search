"""
search/app.py — FastAPI factory.

Routes, request/response models, and startup/shutdown wiring live here.
See templates/ for the HTML side and static/ for assets.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlparse

import zipstream  # streaming ZIP writer for /favorites/download.zip
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from search import config, discover, text_encoder
from search.centroids import (
    CentroidStore,
    DynamicCentroidRegistry,
    DynamicCentroidSpec,
    blend_centroids,
    calibrate_near_dup_threshold,
    composite_centroid_name,
    filter_near_duplicates,
)
from search.diversity import (
    DiversityResultCache,
    DiversityStats,
    rank_diverse,
    relevance_drop_for_mode,
    resolve_depth,
    resolve_mode,
)
from search.image_resolver import guess_content_type, resolve_local, resolve_url
from search.index_db import DEFAULT_INDEX_DB_PATH, ImageNotInCacheError, IndexDB
from search.models import (
    AlbumCreateRequest,
    AlbumDetailResponse,
    AlbumMemberItem,
    AlbumMemberResponse,
    AlbumMembershipsResponse,
    AlbumsListResponse,
    AlbumSummary,
    AlbumUpdateRequest,
    DiscoveryPair,
    DiscoveryPickResponse,
    DiscoveryStartResponse,
    DiversityMetadata,
    ErrorResponse,
    FavoritesListResponse,
    FavoriteToggleResponse,
    SavedSearch,
    SavedSearchCreateRequest,
    SavedSearchListResponse,
    SearchResponse,
    SearchResult,
)
from search.qdrant_client import QdrantSearch, SearchHit
from search.qdrant_url import client_kwargs as _qdrant_client_kwargs
from search.random import RandomPicker

logger = logging.getLogger(__name__)

# Module-level state for the dynamic centroid registry. Initialised
# by `create_app` (or reset by `reset_for_tests`). Tests reach in
# to inspect cache state and to force invalidation; the
# `_invalidate_favourites_centroid` helper below reads this.
_dynamic_centroids = None  # type: DynamicCentroidRegistry | None

# ----- Lazy path-liveness cache (dual-store sync, defense in depth) -----
# Maps absolute path → (exists, expires_at_monotonic). Populated on
# first miss; subsequent checks within the TTL return the cached
# value without touching the filesystem. Bounds the per-request
# stat() cost of the lazy-validation check (`_is_path_alive` below).
# Reads beyond the TTL re-stat. Negative results are cached too —
# a freshly-deleted file shows up as dead within the TTL window.
_path_liveness_cache: dict[str, tuple[bool, float]] = {}
# Memory cap for the lazy liveness cache. At 1.5M points with a
# 60s TTL the steady-state cache stays well under this; the cap is
# a defence against pathological workloads (long-running container
# with millions of distinct paths). Eviction drops the oldest 10%
# of entries by insertion order, which is O(n) on the dict. Bumping
# the cap trades memory for fewer re-stats.
_PATH_LIVENESS_CACHE_MAX: int = 50000


def _is_path_alive(path: str) -> bool:
    """Lazy liveness check for a photo's on-disk path.

    Cached for `_cfg.path_liveness_ttl_seconds` (default 60s). On a
    miss, calls `Path(path).exists()` once and caches the result.
    Both alive and dead outcomes are cached — a deleted file stays
    cached as dead for the TTL window.

    Used by the read paths (/random, /albums/{id}/members, /photo/{id},
    /photo/{id}/raw) to silently skip dead photos without waiting
    for the periodic IndexDB refresh to catch up.

    The TTL is short enough that the cache reflects filesystem
    state within a minute; it's also long enough that a single page
    load (50+ photos) hits `Path.exists()` once, not 50+ times.
    """
    import time as _time
    from pathlib import Path as _Path

    if not path:
        return False
    now = _time.monotonic()
    cached = _path_liveness_cache.get(path)
    if cached is not None:
        exists, expires_at = cached
        if now < expires_at:
            return exists
    try:
        exists = _Path(path).exists()
    except OSError:
        # Permission error, dangling symlink, etc. Treat as dead
        # so the caller skips it. The periodic refresh + scheduled
        # prune will eventually clean up the IndexDB row.
        exists = False
    ttl = _cfg.path_liveness_ttl_seconds
    _path_liveness_cache[path] = (exists, now + ttl)
    # Evict the oldest 10% if we've blown past the cap. Cheap;
    # re-stats on next read. Defends against pathological workloads
    # (long-running container with millions of distinct paths).
    if len(_path_liveness_cache) > _PATH_LIVENESS_CACHE_MAX:
        evict_count = _PATH_LIVENESS_CACHE_MAX // 10
        for _ in range(evict_count):
            try:
                _path_liveness_cache.pop(next(iter(_path_liveness_cache)))
            except StopIteration:
                break
    return exists


def _is_row_alive(row: dict | None) -> bool:
    """True if the row's `path` field is currently alive on disk.

    Defensive wrapper: a None row is dead. Otherwise delegates to
    `_is_path_alive` which caches the filesystem check.
    """
    if not row:
        return False
    return _is_path_alive(str(row.get("path") or ""))


def _coerce_view(raw: str | None) -> str:
    """Return 'grid' or 'feed' from a query-param value; fallback to default."""
    if raw and raw in _cfg.valid_views:
        return raw
    return _cfg.default_view


def _make_favourites_centroid_spec(
    qdrant, index_db,
) -> DynamicCentroidSpec:
    """Build the registration spec for the favourites dynamic centroid.

    Compute path: SELECT id FROM favorites (no images JOIN — orphans
    whose photo is still in Qdrant are still valid; orphans whose
    photo is gone are filtered out by Qdrant retrieve). Then
    batch-retrieve vectors from Qdrant, take the L2-normalised mean.

    Returns None when there are zero favourites or zero retrievable
    vectors, which the registry surfaces as the empty state.

    Takes qdrant + index_db as parameters so the closure captures
    the live instances from create_app rather than relying on
    module-level globals (which would be stale under multiple-app
    test setups).
    """
    import numpy as np

    def compute() -> tuple[list[float], int, list[str]] | None:
        # Snapshot the favourite ids first so a concurrent toggle
        # mid-compute doesn't give us a torn view.
        fav_ids = index_db.list_favorite_ids()
        if not fav_ids:
            return None
        pairs = qdrant.retrieve_batch_with_vectors(fav_ids)
        if not pairs:
            return None
        # Drop any zero-length vectors defensively (shouldn't happen
        # for indexed SigLIP2 vectors but costs nothing to check).
        vectors = np.asarray(
            [v for _, v in pairs if v], dtype=np.float32,
        )
        if vectors.size == 0:
            return None
        # Mean then L2-normalise. The mean of unit vectors is not
        # itself unit-length, so re-normalising is required for
        # Qdrant's cosine-distance search to behave.
        centroid = vectors.mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm == 0:
            return None
        centroid = (centroid / norm).tolist()
        # Seed ids: the ids that actually returned vectors from
        # Qdrant (pairs holds `(id, vec)`). Using `pairs` rather
        # than the raw `fav_ids` strips out orphan ids whose
        # photo is gone from Qdrant — those would generate a
        # no-op exclusion at best and clutter the filter at
        # worst. The third tuple element feeds the dynamic-
        # centroid search route's near-duplicate exclusion.
        seed_ids = [pid for pid, _ in pairs]
        return (centroid, len(vectors), seed_ids)

    return DynamicCentroidSpec(
        name="favourites",
        label="Favourites",
        description=(
            "Mean of every favourited photo's embedding, "
            "re-normalised to unit length. Updates as you favourite."
        ),
        compute_fn=compute,
        source="favourites",
        empty_message=(
            "Favourite a few photos first — this centroid is built "
            "from your favourites list."
        ),
    )


def _invalidate_favourites_centroid() -> None:
    """Drop the cached favourites centroid so the next read recomputes.

    Wired into mark_favorite / unmark_favorite. Safe to call when the
    registry hasn't been initialized yet (e.g. tests that exercise
    the favourites API before create_app runs).
    """
    if _dynamic_centroids is not None:
        _dynamic_centroids.invalidate("favourites")


# ---------------------- Album centroid helpers ----------------------
#
# Each user album auto-registers as a dynamic centroid under the
# name `album:{id}`. The id (not the name) is the key so renames
# don't break existing search/Discover references that pass the
# centroid name to /api/search. The compute closure is essentially
# the same shape as favourites — mean of L2-normalised member
# vectors — so the existing DynamicCentroidSpec machinery handles
# caching, lazy compute, and invalidation for free.

def _album_centroid_name(album_id: int) -> str:
    """Centroid registry key for an album. Stable across renames."""
    return f"album:{album_id}"


def _make_album_centroid_spec(
    qdrant, index_db, album_id: int,
) -> DynamicCentroidSpec:
    """Build a DynamicCentroidSpec for an album.

    Reads the album name lazily inside `compute` so a rename
    surfaces in the next centroid read. The spec's `name` stays
    the id-keyed form (`album:{id}`) — the user-visible label is
    refreshed at every compute call.
    """
    import numpy as np

    def compute() -> tuple[list[float], int, list[str]] | None:
        # Snapshot ids first so a concurrent membership change
        # doesn't give us a torn view.
        ids = index_db.list_album_member_ids(album_id)
        if not ids:
            return None
        pairs = qdrant.retrieve_batch_with_vectors(ids)
        if not pairs:
            return None
        vectors = np.asarray(
            [v for _, v in pairs if v], dtype=np.float32,
        )
        if vectors.size == 0:
            return None
        centroid = vectors.mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm == 0:
            return None
        centroid = (centroid / norm).tolist()
        # Same orphan-stripping pattern as favourites: seed ids
        # are the ids that actually returned vectors. See
        # `_make_favourites_centroid_spec` for the rationale.
        seed_ids = [pid for pid, _ in pairs]
        return (centroid, len(vectors), seed_ids)

    # The static `label` is set at registration time; if the user
    # renames the album, the label goes stale until re-registration.
    # We accept that trade-off because the registry stores one
    # spec per centroid name and the spec doesn't currently support
    # a "label_fn". Renames are infrequent; the stale label is a
    # cosmetic issue, not a correctness one (the centroid itself
    # is always computed fresh from current membership).
    album = index_db.get_album(album_id)
    label = f"Album: {album['name']}" if album else f"Album {album_id}"

    return DynamicCentroidSpec(
        name=_album_centroid_name(album_id),
        label=label,
        description=(
            f"Mean of every photo in album #{album_id}'s embedding, "
            "re-normalised to unit length. Updates as membership "
            "changes."
        ),
        compute_fn=compute,
        source=f"album:{album_id}",
        empty_message=(
            "Add photos to this album to build its centroid."
        ),
    )


def _register_album_centroid(album_id: int) -> None:
    """Register (or re-register) an album's centroid.

    Called from POST /api/albums (new album) and from
    PATCH /api/albums/{id} (rename) so the user-visible label
    refreshes. Idempotent — calling on an already-registered id
    replaces the spec.

    Uses the module-level `_qdrant` / `_index_db` globals set by
    `create_app` so the closure captures the live instances (same
    pattern as `_make_favourites_centroid_spec`). If called
    before create_app has run (e.g. in a test that exercises the
    helpers directly), the lookups return None and we silently
    no-op — the actual registration is wired through the HTTP
    endpoints which always run inside create_app's scope.
    """
    if _dynamic_centroids is None or _qdrant is None or _index_db is None:
        return
    _dynamic_centroids.register(_make_album_centroid_spec(
        _qdrant, _index_db, album_id,
    ))


def _unregister_album_centroid(album_id: int) -> None:
    """Remove an album's centroid from the registry entirely.

    Called from DELETE /api/albums/{id} so the deleted album
    stops appearing on `/centroids` and in `/api/centroids`
    immediately, without waiting for a process restart. Drops
    the spec, any cached value, and any dirty flag in one shot.
    """
    if _dynamic_centroids is None:
        return
    _dynamic_centroids.unregister(_album_centroid_name(album_id))


def _invalidate_album_centroid(album_id: int) -> None:
    """Drop the cached album centroid so the next read recomputes.

    Wired into add_album_member / remove_album_member. Same
    pattern as favourites: invalidate on every membership change
    so the next search reflects the new set.
    """
    if _dynamic_centroids is not None:
        _dynamic_centroids.invalidate(_album_centroid_name(album_id))


HERE = Path(__file__).parent
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"
# Module-level sentinel for FastAPI `Query([])` default — using a literal
# list in a default arg would call `Query()` once at import time, which
# ruff B008 forbids. Use `None` as the default and resolve to a fresh
# list inside the handler.
_EMPTY_COLLECTIONS: list[str] = []


@dataclass(frozen=True)
class PromptState:
    q: str
    positives: list[str]
    negatives: list[str]
    positive_chips: list[str]
    negative_chips: list[str]


# ---------------------- Qdrant client wiring ----------------------

# Module-level references, set by create_app(). Tests can replace
# them with mocks before calling the test client.
_qdrant: QdrantSearch | None = None
_cfg: config.Config | None = None
_centroid_store: CentroidStore | None = None
_index_db: IndexDB | None = None


def get_qdrant() -> QdrantSearch:
    if _qdrant is None:
        raise RuntimeError("QdrantSearch not initialized — call create_app() first")
    return _qdrant


def get_cfg() -> config.Config:
    if _cfg is None:
        raise RuntimeError("Config not initialized — call create_app() first")
    return _cfg


def get_centroid_store() -> CentroidStore:
    if _centroid_store is None:
        raise RuntimeError("CentroidStore not initialized — call create_app() first")
    return _centroid_store


def get_index_db() -> IndexDB:
    if _index_db is None:
        raise RuntimeError("IndexDB not initialized — call create_app() first")
    return _index_db


def reset_for_tests() -> None:
    """Drop module state so the next create_app() rebuilds. Test-only."""
    global _qdrant, _cfg, _centroid_store, _dynamic_centroids, _index_db
    if _index_db is not None:
        with suppress(Exception):
            _index_db.close()
    _qdrant = None
    _cfg = None
    _centroid_store = None
    _dynamic_centroids = None  # DynamicCentroidRegistry, set in create_app
    _index_db = None
    text_encoder.reset_encoder_for_tests()


# ---------------------- App factory ----------------------


def create_app(
    cfg: config.Config | None = None,
    qdrant: QdrantSearch | None = None,
    templates: Jinja2Templates | None = None,
    index_db: IndexDB | None = None,
) -> FastAPI:
    """
    Build a FastAPI app with all routes wired.

    Args:
        cfg: pre-loaded config (defaults to config.load())
        qdrant: pre-built QdrantSearch (defaults to one built from cfg)
        templates: pre-built Jinja2Templates (defaults to one reading
            from search/templates)
        index_db: pre-built IndexDB (defaults to one built from cfg)
    """
    global _qdrant, _cfg, _index_db
    _cfg = cfg or config.load()
    logging.basicConfig(level=_cfg.log_level)

    if qdrant is None:
        from qdrant_client import QdrantClient

        client = QdrantClient(**_qdrant_client_kwargs(
            url=_cfg.qdrant_url,
            api_key=_cfg.qdrant_api_key,
            timeout=_cfg.query_timeout_ms // 1000,  # int seconds
        ))
        qdrant = QdrantSearch(
            client=client,
            collection=_cfg.qdrant_collection,
            timeout_ms=_cfg.query_timeout_ms,
            recommend_timeout_ms=_cfg.recommend_timeout_ms,
        )
    _qdrant = qdrant

    # Build the centroid store from config and load it now so the
    # routes can serve from a populated store. `load()` is total —
    # a missing dir, a corrupt file, or a model/dim mismatch all
    # result in skipping that file (or an empty store), never a
    # crash. Centroids are read-only on the search side: a manual
    # `POST /api/centroids/reload` is the only way to refresh.
    global _centroid_store
    _centroid_store = CentroidStore(
        centroids_dir=Path(_cfg.centroids_dir) if _cfg.centroids_dir else None,
        expected_model=_cfg.centroid_expected_model,
        expected_feature_dim=_cfg.centroid_expected_feature_dim,
    )
    _centroid_store.load()

    if index_db is None:
        db_path = _cfg.index_db_path
        if _cfg.test_mode and db_path == DEFAULT_INDEX_DB_PATH:
            db_path = ":memory:"
        index_db = IndexDB(db_path=db_path, qdrant_client=qdrant)
    _index_db = index_db
    random_picker = RandomPicker(index_db)
    diversity_cache = DiversityResultCache(
        ttl_seconds=_cfg.diversity_cache_ttl_seconds,
        max_entries=_cfg.diversity_cache_max_entries,
    )

    # ---------------- Dynamic centroids ----------------
    #
    # Runtime-computed centroids that complement the disk-loaded
    # `.pt` set. v1 ships one: `favourites`, the mean of every
    # favourited photo's embedding, re-normalised to unit length.
    # Future "themes" register the same way without touching
    # static loading or the search plumbing.
    #
    # IMPORTANT: registered AFTER index_db is initialised (above)
    # so the compute closure captures a live IndexDB, not None.
    global _dynamic_centroids
    _dynamic_centroids = DynamicCentroidRegistry()
    _dynamic_centroids.register(_make_favourites_centroid_spec(qdrant, index_db))
    # Register every existing album as a centroid. New albums
    # register themselves via POST /api/albums → _register_album_centroid;
    # renames via PATCH /api/albums/{id}; deletes via
    # _unregister_album_centroid. This loop only runs on startup
    # to re-attach any albums that survived a process restart.
    # SQLite is fast here (a handful of rows in practice); we
    # don't bother with asyncio.to_thread for what amounts to a
    # sub-millisecond query.
    for existing in index_db.list_albums():
        _dynamic_centroids.register(
            _make_album_centroid_spec(qdrant, index_db, existing["id"])
        )

    templates = templates or Jinja2Templates(directory=str(TEMPLATES_DIR))

    def _strip_query_param(url, name: str, value: str | None = None) -> str:
        """Return `url` with every `name=` param removed.

        When `value` is given, only params matching that exact value
        are removed (use case: remove one centroid from a blend while
        preserving the others). Without `value`, every occurrence of
        `name` is dropped (use case: clear all `centroid=` for the
        'switch to text search' link).

        Always returns a RELATIVE path (scheme + netloc stripped) so
        the chip × links are clean `/?...` style. Operates on the
        URL's raw query string so it round-trips every other param
        (q, positives, negatives, view, favorites, weights, etc.)
        untouched. Accepts both `str` and httpx `URL` (from
        `request.url` in templates — FastAPI hands those to Jinja
        unchanged).
        """
        url_str = str(url)
        parsed = urlparse(url_str)
        params = parse_qsl(parsed.query, keep_blank_values=True)
        if value is None:
            kept = [(k, v) for (k, v) in params if k != name]
        else:
            kept = [(k, v) for (k, v) in params if not (k == name and v == value)]
        new_query = urlencode(kept)
        # path-only output. Re-attach query only if non-empty so
        # the clear-everything case yields `/` not `/?`.
        if new_query:
            return f"{parsed.path}?{new_query}"
        return parsed.path or "/"

    templates.env.filters["strip_query_param"] = _strip_query_param

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Warm the text encoder. In test mode this is a no-op (mock).
        refresh_task: asyncio.Task | None = None
        try:
            text_encoder.get_encoder(test_mode=_cfg.test_mode)
        except Exception as e:
            logger.warning("text encoder warm-up failed: %s", e)
        if not qdrant.healthz():
            logger.warning(
                "Qdrant unreachable at startup (%s) — search will fail until it recovers",
                _cfg.qdrant_url,
            )
        try:
            await asyncio.to_thread(index_db.init_from_qdrant)
        except Exception as e:
            logger.warning("index cache warm-up failed: %s", e)

        # Periodic IndexDB refresh. Picks up bulk indexer runs without
        # the operator having to hit POST /api/cache/refresh manually.
        # The manual endpoint stays as a force-now override. The task
        # is cooperative: `init_from_qdrant` is wrapped in
        # `asyncio.to_thread` so the event loop isn't blocked.
        interval = _cfg.index_db_refresh_interval_seconds
        if interval > 0:
            async def _periodic_refresh_loop() -> None:
                while True:
                    try:
                        await asyncio.sleep(interval)
                        # Take the refresh lock so a manual
                        # POST /api/cache/refresh fired during a
                        # periodic rebuild bails instead of running
                        # a second Qdrant scroll in parallel. The lock
                        # is non-blocking; if the manual path holds
                        # it, we just skip this tick (next tick retries).
                        if not index_db.try_acquire_refresh_lock():
                            logger.debug(
                                "periodic refresh: lock held by another path, skipping this tick"
                            )
                            continue
                        try:
                            t0 = time.time()
                            # `force=True`: must rebuild every tick. The
                            # `force=False` path short-circuits when the
                            # cache is non-empty, which would make the
                            # periodic loop a no-op after the first
                            # warm-up. We want to *always* pick up
                            # Qdrant-side changes (bulk indexer runs,
                            # admin deletes), so force=True is the
                            # right default here. The cost is one
                            # full Qdrant scroll per `interval`
                            # seconds; bounded and acceptable for the
                            # default 6h cadence.
                            count = await asyncio.to_thread(
                                index_db.init_from_qdrant, True
                            )
                            dt_ms = int((time.time() - t0) * 1000)
                            logger.info(
                                "periodic IndexDB refresh: %d rows in %d ms", count, dt_ms,
                            )
                        finally:
                            index_db.release_refresh_lock()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning("periodic IndexDB refresh failed: %s", e)
            refresh_task = asyncio.create_task(_periodic_refresh_loop())
        try:
            yield
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning("refresh task shutdown: %s", e)
            await asyncio.to_thread(index_db.close)

    app = FastAPI(
        title="image-search",
        version="0.1.0",
        lifespan=lifespan,
        # Don't redirect /photo/{id} to /photo/{id}/ (trailing slash).
        redirect_slashes=False,
    )

    @app.middleware("http")
    async def _no_cache_static_middleware(request, call_next):
        """
        Force browsers to re-validate /static/* on every request.

        Why: ES module imports (`import { ... } from "./lib/grid.js"`
        in search.js) are fetched as separate requests, and the
        versioned `?v=N` on the entry-point script doesn't reach
        them. Without `no-cache`, the imported files (grid.js,
        url.js, etc.) get cached for the session and updates to
        them don't appear in the browser. The versioned URL on the
        HTML <link>/<script> becomes the entry point; everything
        else is always re-validated.

        For production with a build step, switch to
        `public, max-age=31536000, immutable` on content-hashed
        filenames instead — this is the dev-friendly default.
        """
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    app.state.index_db = index_db
    app.state.random_picker = random_picker
    app.state.diversity_cache = diversity_cache
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ---------------------- Routes ----------------------

    def _parse_collections(request: Request) -> list[str]:
        """
        Read all `?collection=` query params from the request, in
        stable order. The multi-value shape is what powers the
        chip-style filter UI on the frontend.
        """
        # getlist() preserves order and skips duplicates the way
        # the URL is written; we don't dedupe here because the
        # user might paste a duplicate and the search behavior
        # is the same.
        return [c for c in request.query_params.getlist("collection") if c]

    def _parse_prompts(request: Request, name: str) -> list[str]:
        """
        Read all multi-value prompt params, stripped and in URL order.
        Empty values are ignored so `?positives=` behaves like no chip.
        """
        return [p.strip() for p in request.query_params.getlist(name) if p.strip()]

    def _parse_filename(request: Request) -> str:
        """
        Read the optional `?filename=` query param, stripped.

        Single-value: the existing UI is a single text input. If the
        user ever sends multiple `?filename=` values, the first
        non-empty one wins (consistent with how single-valued
        `?centroid=` works in `_parse_centroid`). Empty /
        whitespace-only returns "" so callers can do the standard
        "if not raw: skip" check.

        The pattern itself is validated later — the helper at the
        top of `create_app` translates the raw string to image ids
        via IndexDB.path_token_ids and surfaces 400 on invalid FTS5
        syntax.
        """
        for raw in request.query_params.getlist("filename"):
            value = raw.strip()
            if value:
                return value
        return ""

    def _parse_centroid(request: Request) -> str | None:
        """
        Read the active centroid from `?centroid=...` (back-compat).

        Returns the FIRST non-empty `?centroid=` value. For new code
        prefer `_parse_centroids` which returns the full list —
        this helper exists so single-centroid call sites and
        response shapes (`centroid: str | None`) keep working
        unchanged. The full multi-centroid contract lives in
        `_parse_centroids` + `centroids` response field.
        """
        for raw in request.query_params.getlist("centroid"):
            value = raw.strip()
            if value:
                return value
        return None

    def _parse_centroids(request: Request) -> list[str]:
        """
        Read every `?centroid=...` value, preserving URL order.

        Empty / whitespace-only values are dropped. Repeated names
        are NOT deduped — the user might want a centroid to count
        twice via weights, and deduping would silently change the
        arithmetic. (E.g. `?centroid=a&centroid=a&weights=1,2`
        legitimately gives weight 3 to a — deduping would shrink
        it to 1.) `blend_centroids` sums weights so a repeated
        name just contributes more to the mean.

        Single `?centroid=a` returns `["a"]` — the call site
        doesn't need a special-case branch.
        """
        return [
            raw.strip()
            for raw in request.query_params.getlist("centroid")
            if raw.strip()
        ]

    def _parse_weights(request: Request, n: int) -> list[float] | None:
        """
        Parse `?weights=` from the request, returning a list of
        length `n` or None to mean "use defaults (all 1.0)".

        Accepted forms (all result in weights for n=2):
          ?weights=1,2          — comma-separated (preferred)
          ?weights=1&weights=2  — repeated params
          ?weights=1.0          — single value broadcast to all n
          (omitted)             — None → defaults

        Negative, zero, and non-numeric values are rejected with
        HTTPException(400) so the user gets immediate feedback
        instead of a silent zero-vector blend downstream.
        """
        if n == 0:
            return []
        raw_values = request.query_params.getlist("weights")
        if not raw_values:
            return None
        # Flatten: each param may itself be comma-separated.
        flat: list[str] = []
        for raw in raw_values:
            flat.extend(p.strip() for p in raw.split(",") if p.strip())
        if not flat:
            return None
        if len(flat) == 1:
            try:
                w = float(flat[0])
            except ValueError as err:
                raise HTTPException(
                    status_code=400,
                    detail=f"weight {flat[0]!r} is not a number",
                ) from err
            if w <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"weights must be positive (got {w})",
                )
            return [w] * n
        if len(flat) != n:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"got {len(flat)} weights for {n} centroids; "
                    f"counts must match (or pass a single weight "
                    f"to broadcast)"
                ),
            )
        try:
            out = [float(x) for x in flat]
        except ValueError as err:
            raise HTTPException(
                status_code=400,
                detail=f"weights must be numbers (got {flat!r})",
            ) from err
        if any(w <= 0 for w in out):
            raise HTTPException(
                status_code=400,
                detail=f"weights must be positive (got {out})",
            )
        return out

    def _resolve_query_vector(
        centroid_names: list[str] | None,
        prompt_state: PromptState,
        weights: list[float] | None = None,
        filename_pattern: str = "",
        centroid_specs: list[DynamicCentroidSpec] | None = None,
    ) -> tuple[list[float], str | None, str | None]:
        """
        Resolve the query vector for a search request.

        Centroid-anchored search is mutually exclusive with text
        search. When `centroid_names` is non-empty, the text prompts
        must be empty (the caller enforces this via `PromptState`).
        Multiple centroid names blend into one vector via
        `blend_centroids` (weighted mean, re-normalised) — see
        `?centroid=&centroid=&weights=` URL contract.

        Filename-only mode (a non-empty `filename_pattern` with no
        prompts and no centroids) returns a zero vector. The
        filename filter is applied separately via `HasId`, so the
        vector's role is reduced to "rank the allowed ids" — and
        cosine similarity to a zero vector is constant for every
        point, so Qdrant falls back to its id-based tie-breaker
        (stable, deterministic ordering by id). The user gets a
        browseable list of the matching files without the "must
        have a positive prompt" error.

        Returns one of three outcomes:

          (vector, None, None)            — success
          ([], "centroid_not_found", ...) — no vector, error tag
          ([], "empty", ...)              — no vector, error tag

        The third tuple slot is a short human-readable detail
        string for the API response and the search page header.
        """
        if centroid_names:
            entries: list[tuple[list[float], str]] = []
            missing: list[str] = []
            for name in centroid_names:
                vec = _fetch_centroid_vector(name)
                if vec is None:
                    missing.append(name)
                    continue
                entries.append((vec, name))
            if missing:
                # All-or-nothing: if any centroid is missing/empty,
                # surface the first missing name. Partial blends are
                # surprising (the user asked for A+B and got just A)
                # so we refuse rather than silently drop one.
                return [], "centroid_not_found", (
                    f"centroid {missing[0]!r} not loaded"
                )
            expected_dim = (
                _cfg.centroid_expected_feature_dim if _cfg else None
            )
            try:
                blended = blend_centroids(
                    entries, weights, expected_dim=expected_dim,
                )
            except ValueError as e:
                return [], "centroid_not_found", str(e)
            return blended, None, None
        if not prompt_state.positives:
            if filename_pattern.strip():
                # Filename-only mode: bypass the prompt requirement
                # and return a zero vector. The filename filter
                # (HasId) and `collections` filter (MatchAny)
                # restrict the candidate set; cosine similarity to
                # the zero vector is constant, so Qdrant falls
                # through to its id-based tie-breaker. The user
                # sees a deterministic, browseable list of the
                # matching files.
                dim = _cfg.centroid_expected_feature_dim if _cfg else None
                if not dim:
                    # Last-resort default for the SigLIP2 model the
                    # project uses. Tests run without a real config
                    # sometimes — this keeps the page rendering.
                    dim = 768
                return [0.0] * dim, None, None
            return [], "empty", "at least one positive prompt is required"
        vec = text_encoder.embed_query_multi(
            tuple(prompt_state.positives),
            tuple(prompt_state.negatives),
        )
        return vec, None, None

    def _fetch_centroid_vector(name: str) -> list[float] | None:
        """Return the vector for a single centroid by name, or None
        if it's unknown / empty. Used by `_resolve_query_vector`
        when blending N centroids. Kept separate so the per-centroid
        fallback (static → dynamic) is consistent with the
        single-centroid path and tested in one place."""
        if _centroid_store is not None:
            spec = _centroid_store.get(name)
            if spec is not None:
                return spec.vector
        if _dynamic_centroids is not None:
            dyn = _dynamic_centroids.get_vector(name)
            if dyn is not None:
                return dyn[0]
            # Dynamic spec exists but has no data yet — distinct
            # from "unknown name". The caller (multi-centroid path)
            # surfaces this as the "not loaded" error rather than
            # silently dropping the entry.
            if _dynamic_centroids.get_spec(name) is not None:
                return None
        return None

    async def _resolve_filename_filter(
        pattern: str,
    ) -> tuple[list[str] | None, str | None]:
        """
        Translate a raw `?filename=` pattern to an `allowed_ids` list
        for Qdrant's `HasId` filter, applying the cardinality guard.

        Returns one of four outcomes (always a 2-tuple):

          (None, None)            — no filter requested (pattern
                                    was empty), OR the guard decided
                                    to skip the filter because
                                    matching ids cover > 50% of
                                    the cache. Caller passes `None`
                                    to qdrant.search meaning "no
                                    HasId filter".
          ([], None)              — pattern matched zero images.
                                    Caller surfaces an empty result
                                    (the user typed
                                    `?filename=nonsense`).
          (list[str], None)       — a usable set of allowed ids.
          (None, "bad_request")   — pattern was syntactically
                                    invalid (caller surfaces a 400
                                    with the validator's message
                                    via `path_token_ids`).

        The cardinality guard is the load-bearing piece: when a
        pattern like `2024` matches 60% of the collection, applying
        `HasId` to the rest actively hurts search quality (HNSW
        has to pick the top-K from a non-uniform distribution AND
        serialise the id list on every request) without meaningfully
        narrowing anything. By dropping the filter above the guard
        threshold we get "full ranking + 0 wasted work", which is
        the right default for very-broad queries.

        The guard uses the SQLite cache rowcount as the denominator
        — it's already loaded and O(1) to read. For the 1.5M-row
        live cache that's a free comparison.

        Total count is fetched via `await asyncio.to_thread` so it
        doesn't block the event loop on the (cheap) SQLite read.
        """
        if not pattern or not pattern.strip():
            return None, None
        try:
            ids = await asyncio.to_thread(
                index_db.path_token_ids, pattern
            )
        except ValueError:
            # Pattern failed FTS5 validation (e.g. leading `*`,
            # multi-token input, etc). Surface as a 400 — the
            # validator's message is already user-friendly and
            # includes the offending pattern, so we just signal
            # the route handler to wrap it.
            return None, "bad_request"
        if ids is None:
            # Empty pattern after sanitisation. Same as no filter.
            return None, None
        if not ids:
            # Pattern matched zero images. Distinguish this from
            # "skip the filter" so the caller can render an empty
            # results state with the right message (rather than
            # ranking over the whole collection).
            return [], None
        # Cardinality guard. The guard is symmetric: we apply it
        # whether the filter is super-narrow or super-broad — both
        # are degenerate. The > 0.5 threshold matches the design
        # discussion; below it the filter strictly improves latency
        # and relevance; above it the filter competes with HNSW
        # scoring without adding value.
        total = await asyncio.to_thread(index_db.count_images)
        if total > 0:
            coverage = len(ids) / total
            if coverage > _cfg.filename_cardinality_guard:
                logger.info(
                    "filename filter %r matched %d/%d (%.1f%%); "
                    "skipping HasId per cardinality guard",
                    pattern, len(ids), total, coverage * 100,
                )
                return None, None
        return ids, None

    def _normalize_prompt_state(
        q: str,
        positives_raw: list[str],
        negatives_raw: list[str],
    ) -> PromptState:
        """
        Normalize q/positive/negative prompt inputs for search.

        Display text is preserved for response/template echo. Dedupe is
        case-insensitive per side, overlong prompts are dropped, and q is
        appended to positives if it is a usable non-duplicate prompt.
        """
        effective_q = (q or "").strip()
        positive_keys: set[str] = set()
        negative_keys: set[str] = set()
        positive_entries: list[tuple[str, bool]] = []
        negative_entries: list[tuple[str, bool]] = []

        def add_positive(text: str) -> None:
            prompt = text.strip()
            key = prompt.lower()
            if not prompt or len(prompt) > _cfg.max_prompt_chars or key in positive_keys:
                return
            positive_keys.add(key)
            positive_entries.append((prompt, True))

        def add_negative(text: str) -> None:
            prompt = text.strip()
            key = prompt.lower()
            if not prompt or len(prompt) > _cfg.max_prompt_chars or key in negative_keys:
                return
            negative_keys.add(key)
            negative_entries.append((prompt, True))

        for prompt in positives_raw:
            add_positive(prompt)
        if effective_q:
            prompt = effective_q
            key = prompt.lower()
            if len(prompt) <= _cfg.max_prompt_chars and key not in positive_keys:
                positive_keys.add(key)
                positive_entries.append((prompt, False))
        for prompt in negatives_raw:
            add_negative(prompt)

        remaining = _cfg.max_prompts_total
        capped_positive_entries = positive_entries[:remaining]
        remaining -= len(capped_positive_entries)
        capped_negative_entries = negative_entries[:remaining]
        positives = [prompt for prompt, _explicit in capped_positive_entries]
        negatives = [prompt for prompt, _explicit in capped_negative_entries]
        return PromptState(
            q=effective_q,
            positives=positives,
            negatives=negatives,
            positive_chips=[
                prompt for prompt, explicit in capped_positive_entries if explicit
            ],
            negative_chips=[
                prompt for prompt, explicit in capped_negative_entries if explicit
            ],
        )

    def _search_query_string(
        q: str,
        positives: list[str],
        negatives: list[str],
        collections: list[str],
        view: str = _cfg.default_view,
        centroid: str | None = None,
        favorites: bool = False,
        centroids: list[str] | None = None,
        weights: list[float] | None = None,
        diverse: bool = False,
        diversity_mode: str = "off",
        diversity_depth: str = "auto",
        filename: str = "",
    ) -> str:
        """
        Build a multi-value search-state query string for photo back links.

        `view` is included so the user lands back on the same view they
        came from. We omit it when it's the default ('grid') to keep
        canonical URLs clean. Centroid state round-trips via repeated
        `?centroid=` params (with `?weights=` when not all-equal) so the
        "view tile → back" path lands on the same centroid search the
        user came from, not a bare `/`.

        `centroids` is the canonical input — a list of centroid names
        in blend order. The legacy `centroid` param is kept for
        single-centroid call sites and emits one `?centroid=` param.
        When `centroids` is supplied (even length 1), it takes
        precedence — the function never emits the same centroid twice
        from both inputs.

        `filename` round-trips the path-substring filter so the
        "view tile → back" link returns to the same narrowed search
        the user came from. Empty / whitespace-only is omitted so
        canonical URLs stay clean. The filter is single-valued, so a
        plain `?filename=` param (no list).
        """
        params: list[tuple[str, str]] = []
        if q:
            params.append(("q", q))
        params.extend(("positives", p) for p in positives)
        params.extend(("negatives", n) for n in negatives)
        params.extend(("collection", c) for c in collections)
        if centroids is not None:
            params.extend(("centroid", c) for c in centroids)
        elif centroid:
            params.append(("centroid", centroid))
        if weights is not None and any(w != 1.0 for w in weights):
            params.append(("weights", ",".join(str(w) for w in weights)))
        if favorites:
            params.append(("favorites", "true"))
        if filename.strip():
            params.append(("filename", filename.strip()))
        if view and view != _cfg.default_view:
            params.append(("view", view))
        if diversity_mode and diversity_mode != "off":
            params.append(("diversity", diversity_mode))
            if diversity_depth and diversity_depth != "auto":
                params.append(("diversity_depth", diversity_depth))
        elif diverse:
            # Legacy callers that only know the boolean retain the old
            # URL shape; current search pages emit the explicit mode.
            params.append(("diverse", "true"))
        return urlencode(params)

    def _surprise_search(
        hits: list[SearchHit],
        k: int,
    ) -> list[SearchHit]:
        """Shuffle hits and return up to k. Non-deterministic."""
        shuffled = list(hits)
        random.shuffle(shuffled)
        return shuffled[:k]

    def _favorite_id_set_sync(point_ids: list[str]) -> set[str]:
        favorites: set[str] = set()
        for pid in point_ids:
            row = index_db.get_by_id(pid)
            if row and int(row.get("is_favorite") or 0) == 1:
                favorites.add(pid)
        return favorites

    async def _favorite_id_set(point_ids: list[str]) -> set[str]:
        return await asyncio.to_thread(_favorite_id_set_sync, point_ids)

    async def _results_from_hits(hits: list, favorite_ids: set[str] | None = None) -> list[SearchResult]:
        if favorite_ids is None:
            favorite_ids = await _favorite_id_set([h.id for h in hits])
        return [
            SearchResult(
                id=h.id,
                path=h.path,
                score=h.score,
                score_str=f"{h.score:.3f}",
                url=resolve_url(h.id, _cfg.web_ui_url),
                is_favorite=h.id in favorite_ids,
                # LQIP from the Qdrant payload (set at index time, T9).
                # None when the point was indexed before blurhash landed.
                blurhash=(h.payload or {}).get("blurhash"),
            )
            for h in hits
        ]

    async def _favorite_ids_for_filter() -> set[str]:
        rows = await asyncio.to_thread(index_db.list_favorites, _cfg.max_results_total, 0)
        return {str(row["id"]) for row in rows}

    def _diversity_metadata(stats: DiversityStats) -> DiversityMetadata:
        return DiversityMetadata(
            requested=stats.requested,
            applied=stats.applied,
            mode=stats.mode,
            strength=stats.strength,
            candidate_count=stats.candidate_count,
            result_count=stats.result_count,
            duplicate_images_collapsed=stats.duplicate_images_collapsed,
            semantic_groups_covered=stats.semantic_groups_covered,
            depth=stats.depth,
            pool_depth=stats.pool_depth,
        )

    def _digest_values(values: list[str] | set[str] | None) -> str:
        digest = hashlib.sha256()
        if values is None:
            digest.update(b"<none>\0")
        for value in sorted(str(item) for item in (values or [])):
            digest.update(value.encode("utf-8", "replace"))
            digest.update(b"\0")
        return digest.hexdigest()[:20]

    def _diversity_cache_key(
        vector: list[float],
        mode: str,
        depth: str,
        pool_depth: int,
        collections: list[str],
        allowed_ids: list[str] | None,
        favorite_ids: set[str] | None,
    ) -> str:
        vector_digest = hashlib.sha256(
            repr(tuple(round(float(value), 8) for value in vector)).encode("ascii")
        ).hexdigest()[:20]
        return "|".join((
            _cfg.qdrant_collection,
            mode,
            depth,
            str(pool_depth),
            vector_digest,
            _digest_values(collections),
            _digest_values(allowed_ids),
            _digest_values(favorite_ids),
        ))

    def _diversity_page(
        vector: list[float],
        effective_limit: int,
        offset: int,
        collections: list[str],
        allowed_ids: list[str] | None,
        favorite_ids: set[str] | None,
        mode: str,
        strength: float,
        depth: str,
        pool_depth: int,
    ) -> tuple[list[SearchHit], bool, DiversityMetadata]:
        """Build or retrieve one complete, stable Diversity ordering."""
        cache_key = _diversity_cache_key(
            vector, mode, depth, pool_depth, collections, allowed_ids, favorite_ids,
        )
        cached = diversity_cache.get(cache_key)
        if cached is not None:
            hits = list(cached.hits)
            page = hits[offset:offset + effective_limit]
            return page, len(hits) > offset + effective_limit, _diversity_metadata(cached.stats)

        search_allowed_ids = allowed_ids
        if favorite_ids is not None:
            favorite_list = sorted(favorite_ids)
            if search_allowed_ids is None:
                search_allowed_ids = favorite_list
            else:
                favorite_set = set(favorite_list)
                search_allowed_ids = [
                    point_id for point_id in search_allowed_ids
                    if point_id in favorite_set
                ]
            if not search_allowed_ids:
                stats = DiversityStats(
                    requested=True, applied=True, mode=mode, strength=strength,
                    depth=depth, pool_depth=0,
                )
                return [], False, _diversity_metadata(stats)

        # Fetch from offset zero and rank the complete candidate universe before
        # slicing. The requested depth is independent from Diversity strength:
        # a deep, low-strength search can still preserve relevance while making
        # more candidates available for later pages.
        # ``resolve_depth`` already maps Auto to a distinct depth for each
        # Diversity mode. Do not apply a second configurable floor here: a
        # user-configured base pool could otherwise collapse Low and
        # Balanced (for example, both would become 1,000).
        requested_pool_depth = pool_depth
        # Candidate depth is intentionally independent from the cumulative
        # result cap. We may rank a deep pool to improve the first N results
        # even when MAX_RESULTS_TOTAL limits how far the user can scroll.
        candidate_limit = min(
            _cfg.diversity_max_candidate_pool_size,
            requested_pool_depth,
        )
        pairs, _ = qdrant.search_with_vectors(
            vector,
            limit=candidate_limit,
            offset=0,
            collections=collections or None,
            allowed_ids=search_allowed_ids,
        )
        ranking = rank_diverse(
            pairs,
            vector,
            mode=mode,
            strength=strength,
            duplicate_hamming_distance=_cfg.diversity_duplicate_hamming_distance,
            relevance_drop=relevance_drop_for_mode(
                mode, _cfg.diversity_relevance_drop,
            ),
            max_results=_cfg.max_results_total,
            depth=depth,
            pool_depth=len(pairs),
        )
        diversity_cache.put(cache_key, ranking.hits, ranking.stats)
        page = ranking.hits[offset:offset + effective_limit]
        return page, len(ranking.hits) > offset + effective_limit, _diversity_metadata(ranking.stats)

    @app.get("/", response_class=HTMLResponse)
    async def search_page(
        request: Request,
        q: str = Query("", description="text query"),
        limit: int = Query(_cfg.top_k_default, description="max results"),
        offset: int = Query(0, description="offset into the full result set"),
        view: str = Query(_cfg.default_view, description="result view: 'grid' or 'feed'"),
        favorites: bool = Query(False, description="restrict results to favourites"),
        diverse: bool = Query(False, description="apply MMR diversity re-ranking"),
        diversity: str | None = Query(
            None, description="Diversity strength: off, low, balanced, or high",
        ),
        diversity_depth: str | None = Query(
            None, description="Diversity candidate depth: auto, 500, 1000, 2000, or 5000",
        ),
        surprise: bool = Query(False, description="Surprise Me — random sample from deep pool"),
    ) -> HTMLResponse:
        view = _coerce_view(view)
        diversity_error: str | None = None
        try:
            diversity_mode, diversity_strength = resolve_mode(diversity, diverse)
        except ValueError as exc:
            diversity_mode, diversity_strength = "off", 0.0
            diversity_error = str(exc)
        try:
            diversity_depth_mode, diversity_pool_depth = resolve_depth(
                diversity_depth, diversity_mode,
            )
        except ValueError as exc:
            diversity_depth_mode, diversity_pool_depth = "auto", 0
            diversity_error = diversity_error or str(exc)
        diverse = diversity_mode != "off"
        # Clamp limit here so the form's "?limit=99999" still works
        # (server-rendered pages render whatever limit is given, just
        # capped). The /api/search endpoint returns 400 on out-of-range.
        try:
            limit = max(1, min(int(limit), _cfg.top_k_max))
        except (TypeError, ValueError):
            limit = _cfg.top_k_default
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        # Hard cap on total cumulative results served. Once the user
        # scrolls past this, has_more is False even if more exist.
        if offset >= _cfg.max_results_total:
            offset = _cfg.max_results_total
            limit = 0

        collections = _parse_collections(request)
        prompt_state = _normalize_prompt_state(
            q,
            _parse_prompts(request, "positives"),
            _parse_prompts(request, "negatives"),
        )
        active_centroids = _parse_centroids(request)
        active_weights = _parse_weights(request, len(active_centroids))
        active_centroid = active_centroids[0] if active_centroids else None
        filename_pattern = _parse_filename(request)
        # Resolve `allowed_ids` from the filename pattern up front so
        # any 400 lands before the long-running Qdrant search. Empty
        # / no-op patterns short-circuit to None here.
        allowed_ids, fname_err = await _resolve_filename_filter(
            filename_pattern
        )
        if fname_err == "bad_request":
            return _bad_request(filename_pattern and
                f"invalid filename pattern: {filename_pattern!r}" or
                "invalid filename pattern"
            )

        # Mutex: a centroid search cannot coexist with text prompts.
        # The user is asked to pick one. A friendly error renders on
        # the page (no API call is made).
        if active_centroids and (prompt_state.q or prompt_state.positives or prompt_state.negatives):
            return templates.TemplateResponse(
                request,
                "search.html",
                {
                    "q": prompt_state.q,
                    "positives": prompt_state.positives,
                    "negatives": prompt_state.negatives,
                    "positive_chips": prompt_state.positive_chips,
                    "negative_chips": prompt_state.negative_chips,
                    "collections": collections,
                    "view": view,
                    "favorites_filter": favorites,
                    "diverse": diverse,
                    "diversity_mode": diversity_mode,
                    "diversity_depth": diversity_depth_mode,
                    "filename": filename_pattern,
                    "search_query_string": _search_query_string(
                        prompt_state.q,
                        prompt_state.positive_chips,
                        prompt_state.negative_chips,
                        collections,
                        view=view,
                        centroids=active_centroids,
                        weights=active_weights,
                        favorites=favorites,
                        diverse=diverse,
                        diversity_mode=diversity_mode,
                        diversity_depth=diversity_depth_mode,
                        filename=filename_pattern,
                    ),
                    "limit": limit,
                    "offset": offset,
                    "has_more": False,
                    "max_results_total": _cfg.max_results_total,
                    "results": [],
                    "error": (
                        f"Centroid search is exclusive — cannot combine ?centroid="
                        f"{composite_centroid_name(active_centroids)!r} with text prompts."
                    ),
                    "took_ms": 0,
                    "top_k_default": _cfg.top_k_default,
                    "active_centroid": None,
                    "active_centroids": [],
                    "active_weights": active_weights,
                    "static_assets_version": _cfg.static_assets_version,
                },
            )

        results: list[dict] = []
        error: str | None = diversity_error
        took_ms: int = 0
        has_more = False
        attempted_search = bool(
            active_centroids
            or prompt_state.q
            or request.query_params.getlist("positives")
            or request.query_params.getlist("negatives")
            or filename_pattern
        )

        if surprise and diverse:
            error = "Diversity cannot be combined with Surprise Me. Choose one search mode."
        elif attempted_search and not active_centroids and not prompt_state.positives and not filename_pattern and not surprise and not error:
            error = "At least one positive prompt is required."
        elif attempted_search and limit > 0 and not error:
            if surprise and not prompt_state.positives and not active_centroids:
                # Surprise with no query: use zero vector so Qdrant
                # returns results from the whole collection.
                dim = _cfg.centroid_expected_feature_dim
                vec = [0.0] * dim
                vec_err = None
                vec_detail = None
            else:
                vec, vec_err, vec_detail = _resolve_query_vector(
                    active_centroids, prompt_state, weights=active_weights,
                    filename_pattern=filename_pattern,
                )
            if vec_err == "centroid_not_found":
                error = (
                    f"Centroid {active_centroid!r} is not loaded."
                    if active_centroids and len(active_centroids) == 1
                    else vec_detail
                    or "one of the centroids is not loaded"
                )
            elif vec_err == "empty":
                error = "At least one positive prompt is required."
            else:
                t0 = time.time()
                # `allowed_ids == []` is a legitimate outcome from
                # `_resolve_filename_filter`: the pattern was valid
                # but matched zero images. The user expected an
                # empty result list, NOT a full ranking — skip the
                # Qdrant call entirely and return zero hits. (We
                # can't pass `has_id=[]` to HasIdCondition because
                # Qdrant treats an empty list as "filter off" rather
                # than "match nothing". Skipping the round-trip is
                # cheaper and clearer.)
                if allowed_ids is not None and not allowed_ids:
                    hits: list = []
                    has_more = False
                    results: list[dict] = []
                    took_ms = int((time.time() - t0) * 1000)
                else:
                    try:
                        # Don't let one page exceed the total cap.
                        effective_limit = min(limit, _cfg.max_results_total - offset)
                        diversity_meta = DiversityMetadata()
                        if diverse:
                            favorite_ids = await _favorite_ids_for_filter() if favorites else None
                            hits, has_more, diversity_meta = _diversity_page(
                                vec,
                                effective_limit,
                                offset,
                                collections,
                                allowed_ids,
                                favorite_ids,
                                diversity_mode,
                                diversity_strength,
                                diversity_depth_mode,
                                diversity_pool_depth,
                            )
                            results = await _results_from_hits(hits, favorite_ids)
                        elif favorites:
                            favorite_ids = await _favorite_ids_for_filter()
                            hits_all, _ = qdrant.search(
                                vec, limit=_cfg.max_results_total, offset=0,
                                collections=collections or None,
                                allowed_ids=allowed_ids,
                            )
                            favorite_hits = [h for h in hits_all if h.id in favorite_ids]
                            hits = favorite_hits[offset:offset + effective_limit]
                            has_more = len(favorite_hits) > offset + effective_limit
                            results = await _results_from_hits(hits, favorite_ids)
                        elif surprise:
                            pool = _cfg.surprise_pool_size
                            k = _cfg.surprise_result_count
                            hits, _ = qdrant.search(
                                vec, limit=pool, offset=0,
                                collections=collections or None,
                                allowed_ids=allowed_ids,
                            )
                            hits = _surprise_search(hits, k)
                            has_more = False
                            results = await _results_from_hits(hits)
                        else:
                            hits, has_more = qdrant.search(
                                vec, limit=effective_limit, offset=offset,
                                collections=collections or None,
                                allowed_ids=allowed_ids,
                            )
                            results = await _results_from_hits(hits)
                        took_ms = int((time.time() - t0) * 1000)
                    except (ConnectionError, OSError) as e:
                        took_ms = int((time.time() - t0) * 1000)
                        logger.warning("Qdrant unreachable for /: %s", e)
                        error = "Search is currently unavailable."
                    except Exception:
                        took_ms = int((time.time() - t0) * 1000)
                        logger.exception("search failed")
                        error = "Search is currently unavailable."

        # When the landing page is hit with no query and no centroid,
        # surface a small random sample below the form. Reuses the
        # same SQLite sample path as /random so the cache stays the
        # single source of truth. Skipped on error paths so a stale
        # random block doesn't paper over a real failure message.
        random_picks: list[dict] = []
        if (
            not error
            and not active_centroids
            and not prompt_state.q
            and not prompt_state.positives
            and not prompt_state.negatives
            and not filename_pattern
        ):
            try:
                random_rows = await asyncio.to_thread(
                    index_db.pick_random_rows, HOME_RANDOM_PICKS
                )
                random_picks = [r.model_dump() for r in _random_rows_to_results(random_rows)]
            except Exception as e:
                # Random picks are a nicety, not critical. If the
                # sample fails, render the page without them.
                logger.warning("home random picks failed: %s", e)

        # Saved-search dropdown population. Pull up to 200 rows — the
        # dropdown is a flat list with no pagination, and most users
        # accumulate tens, not thousands. Failure here shouldn't
        # break the page (the dropdown just shows the default
        # "pick a saved search" option only).
        try:
            saved_rows, _ = await asyncio.to_thread(
                index_db.list_saved_searches, 200, 0,
            )
            saved_searches_for_template = [
                {
                    "id": int(r["id"]),
                    "name": str(r["name"]),
                    "positives": list(r.get("positives") or []),
                    "negatives": list(r.get("negatives") or []),
                }
                for r in saved_rows
            ]
        except Exception as e:
            logger.warning("saved searches list failed: %s", e)
            saved_searches_for_template = []

        return templates.TemplateResponse(
            request,
            "search.html",
            {
                "q": prompt_state.q,
                "positives": prompt_state.positives,
                "negatives": prompt_state.negatives,
                "positive_chips": prompt_state.positive_chips,
                "negative_chips": prompt_state.negative_chips,
                "collections": collections,
                "view": view,
                "favorites_filter": favorites,
                "diverse": diverse,
                "diversity_mode": diversity_mode,
                "diversity_depth": diversity_depth_mode,
                "filename": filename_pattern,
                "search_query_string": _search_query_string(
                    prompt_state.q,
                    prompt_state.positive_chips,
                    prompt_state.negative_chips,
                    collections,
                    view=view,
                    centroid=active_centroid,
                    favorites=favorites,
                    diverse=diverse,
                    diversity_mode=diversity_mode,
                    diversity_depth=diversity_depth_mode,
                    filename=filename_pattern,
                ),
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "max_results_total": _cfg.max_results_total,
                "results": results,
                "random_picks": random_picks,
                "random_picks_count": len(random_picks),
                "error": error,
                "took_ms": took_ms,
                "top_k_default": _cfg.top_k_default,
                "active_centroid": active_centroid,
                "active_centroids": active_centroids,
                "active_weights": active_weights,
                "saved_searches": saved_searches_for_template,
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    @app.get("/photo/{point_id}", response_class=HTMLResponse)
    async def photo_page(
        request: Request,
        point_id: str,
        q: str = Query("", description="originating query string for back link"),
        view: str = Query(_cfg.default_view, description="originating view for back link"),
        favorites: bool = Query(False, description="originating favourites filter"),
        from_favorites: bool = Query(False, description="return to favourites page"),
        diverse: bool = Query(False, description="originating legacy Diversity flag"),
        diversity: str | None = Query(
            None, description="originating Diversity strength",
        ),
        diversity_depth: str | None = Query(
            None, description="originating Diversity candidate depth",
        ),
    ) -> HTMLResponse:
        view = _coerce_view(view)
        try:
            diversity_mode, _ = resolve_mode(diversity, diverse)
        except ValueError:
            # A stale or hand-edited photo URL should still render. Fall back
            # to the safe baseline rather than carrying an invalid mode back
            # into search.
            diversity_mode = "off"
        try:
            diversity_depth_mode, _ = resolve_depth(diversity_depth, diversity_mode)
        except ValueError:
            diversity_depth_mode = "auto"
        diverse = diversity_mode != "off"
        try:
            hit = qdrant.retrieve(point_id)
        except (ConnectionError, OSError) as e:
            logger.warning("Qdrant unreachable for /photo/%s: %s", point_id, e)
            raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
        if hit is None:
            raise HTTPException(status_code=404, detail="Photo not found")
        # Lazy liveness: if the file is gone from disk, 404 immediately.
        # The raw-image route would 404 anyway, but a 404 at the page
        # level is a cleaner signal than rendering "File not found" in
        # the middle of the photo detail page.
        if not _is_path_alive(str(hit.path)):
            raise HTTPException(status_code=404, detail="Photo file missing")

        local = resolve_local(hit.path, _cfg.nas_images_base, _cfg.path_prefix)
        file_missing = local is None
        # Belt-and-braces: even if `resolve_local` succeeded, the file
        # may have been deleted since the last IndexDB refresh.
        if not file_missing and not _is_path_alive(str(local)):
            file_missing = True
        prompt_state = _normalize_prompt_state(
            q,
            _parse_prompts(request, "positives"),
            _parse_prompts(request, "negatives"),
        )
        collections = _parse_collections(request)
        # Preserve `?centroid=&centroid=&weights=` so the back button
        # returns the user to the centroid search they came from,
        # not a bare `/`. The JS already forwards it on the photo
        # link (currentSearchParams), so we just need to read it
        # back here.
        active_centroids = _parse_centroids(request)
        active_weights = _parse_weights(request, len(active_centroids))
        active_centroids[0] if active_centroids else None
        filename_pattern = _parse_filename(request)
        cached_row = await asyncio.to_thread(index_db.get_by_id, hit.id)
        is_favorite = bool(cached_row and int(cached_row.get("is_favorite") or 0) == 1)
        # Lazy liveness for the /photo page too (same check as the
        # raw route). Defensive: catches filesystem deletions that the
        # IndexDB refresh hasn't caught up with yet.
        # All user albums + which ones contain this photo, for the
        # album pill toggles on the photo detail page. Loaded in
        # parallel via gather so the photo page latency stays flat
        # regardless of album count.
        import asyncio as _asyncio
        all_albums, photo_albums = await _asyncio.gather(
            asyncio.to_thread(index_db.list_albums),
            asyncio.to_thread(index_db.list_albums_for_favorite, hit.id),
        )
        photo_album_ids = {str(a["id"]) for a in photo_albums}
        return templates.TemplateResponse(
            request,
            "photo.html",
            {
                "id": hit.id,
                "path": hit.path,
                "url": resolve_url(hit.id, _cfg.web_ui_url),
                "q": prompt_state.q,
                "positives": prompt_state.positives,
                "negatives": prompt_state.negatives,
                "positive_chips": prompt_state.positive_chips,
                "negative_chips": prompt_state.negative_chips,
                "collections": collections,
                "view": view,
                "favorites_filter": favorites,
                "from_favorites": from_favorites,
                "filename": filename_pattern,
                "search_query_string": _search_query_string(
                    prompt_state.q,
                    prompt_state.positive_chips,
                    prompt_state.negative_chips,
                    collections,
                    view=view,
                    centroids=active_centroids,
                    weights=active_weights,
                    favorites=favorites,
                    diverse=diverse,
                    diversity_mode=diversity_mode,
                    diversity_depth=diversity_depth_mode,
                    filename=filename_pattern,
                ),
                "payload": hit.payload or {},
                "file_missing": file_missing,
                "is_favorite": is_favorite,
                "all_albums": all_albums,
                "photo_album_ids": photo_album_ids,
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    @app.get("/photo/{point_id}/raw")
    async def photo_raw(point_id: str) -> FileResponse:
        try:
            hit = qdrant.retrieve(point_id)
        except (ConnectionError, OSError) as e:
            logger.warning("Qdrant unreachable for /photo/%s/raw: %s", point_id, e)
            raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
        if hit is None:
            raise HTTPException(status_code=404, detail="Photo not found")
        # Lazy liveness at the raw route too. The page route has its
        # own check; this is the last line of defense before serving
        # bytes from disk.
        if not _is_path_alive(str(hit.path)):
            raise HTTPException(status_code=404, detail="Photo file missing")

        local = resolve_local(hit.path, _cfg.nas_images_base, _cfg.path_prefix)
        if local is None or not _is_path_alive(str(local)):
            raise HTTPException(status_code=404, detail="File not found on disk")

        filename = local.name
        return FileResponse(
            local,
            media_type=guess_content_type(local),
            filename=filename,
            content_disposition_type="inline",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/photo/{point_id}/similar", response_class=HTMLResponse)
    async def photo_similar(
        request: Request,
        point_id: str,
        view: str = Query(_cfg.default_view, description="result view for grid"),
    ) -> HTMLResponse:
        """
        Render the top-K most similar images to the given photo.

        The source photo is included in result #1 with score ~1.0
        (by design — acts as a sanity check that the right vector
        was retrieved). Cross-collection by default: no `collections`
        filter is applied, so the result set spans every library
        indexed under the configured Qdrant collection.

        Two Qdrant round-trips per request:
          1. retrieve_with_vector(point_id) — get the source embedding
          2. query_points(query=<vec>, limit=70) — top-K HNSW
        Both are O(1) / O(log N). Worst case latency is the sum of
        the two timeout windows; the second call dominates.
        """
        view = _coerce_view(view)
        try:
            fetched = qdrant.retrieve_with_vector(point_id)
        except (ConnectionError, OSError) as e:
            logger.warning(
                "Qdrant unreachable for /photo/%s/similar: %s", point_id, e
            )
            raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
        if fetched is None:
            raise HTTPException(status_code=404, detail="Photo not found")
        vec, hit = fetched

        t0 = time.time()
        try:
            # No `collections` filter → cross-collection. The photo's
            # own vector is reused as the query vector, so result #1
            # is always the source itself at score ~1.0. That's
            # intentional: it doubles as a "this is the right point"
            # confirmation when the page renders.
            hits, _ = qdrant.search(vec, limit=70)
        except (ConnectionError, OSError) as e:
            logger.warning(
                "Qdrant unreachable during similar-search for %s: %s",
                point_id, e,
            )
            raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
        took_ms = int((time.time() - t0) * 1000)

        results = await _results_from_hits(hits)

        return templates.TemplateResponse(
            request,
            "search.html",
            {
                # Empty `q` and no prompt chips: the search input
                # renders blank, the prompt-composition rows show
                # "add prompt" placeholders (same as a fresh home
                # page state).
                "q": "",
                "positives": [],
                "negatives": [],
                "positive_chips": [],
                "negative_chips": [],
                "collections": [],
                "view": view,
                "results": results,
                # Mirror the SearchResponse shape so the template
                # has no special cases for the result grid.
                "offset": 0,
                "limit": 70,
                "has_more": False,  # K=70 is the whole answer, no pagination
                "took_ms": took_ms,
                "max_results_total": 70,
                # Mode marker: when set, search.html branches into
                # the "most similar" header + back-link shape.
                "source_photo_id": hit.id,
                "source_photo_path": hit.path,
                "search_query_string": "",  # back button goes to /photo/{id}, not /?...
                "active_centroid": None,  # this route is text/photo-anchored, never centroid
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    @app.get("/api/search", response_model=SearchResponse)
    async def api_search(
        request: Request,
        q: str = Query("", description="text query"),
        limit: int = Query(_cfg.top_k_default, description="max results"),
        offset: int = Query(0, description="offset into the full result set"),
        view: str = Query(_cfg.default_view, description="result view: 'grid' or 'feed'"),
        favorites: bool = Query(False, description="restrict results to favourites"),
        diverse: bool = Query(False, description="apply MMR diversity re-ranking"),
        diversity: str | None = Query(
            None, description="Diversity strength: off, low, balanced, or high",
        ),
        diversity_depth: str | None = Query(
            None, description="Diversity candidate depth: auto, 500, 1000, 2000, or 5000",
        ),
        surprise: bool = Query(False, description="Surprise Me — random sample from deep pool"),
    ):
        # Manual validation so we return 400 (not 422) for bad input.
        view = _coerce_view(view)
        try:
            diversity_mode, diversity_strength = resolve_mode(diversity, diverse)
        except ValueError as exc:
            return _bad_request(str(exc))
        try:
            diversity_depth_mode, diversity_pool_depth = resolve_depth(
                diversity_depth, diversity_mode,
            )
        except ValueError as exc:
            return _bad_request(str(exc))
        diverse = diversity_mode != "off"
        if surprise and diverse:
            return _bad_request(
                "Diversity cannot be combined with Surprise Me. Choose one search mode."
            )
        prompt_state = _normalize_prompt_state(
            q,
            _parse_prompts(request, "positives"),
            _parse_prompts(request, "negatives"),
        )
        active_centroids = _parse_centroids(request)
        active_weights = _parse_weights(request, len(active_centroids))
        active_centroid = active_centroids[0] if active_centroids else None
        filename_pattern = _parse_filename(request)
        # Resolve `allowed_ids` from the filename pattern up front so
        # any 400 lands before the long-running Qdrant search.
        allowed_ids, fname_err = await _resolve_filename_filter(
            filename_pattern
        )
        if fname_err == "bad_request":
            # The validator's message is already user-friendly; we
            # surface it verbatim. (PathTokenIds raises ValueError
            # with a clear string, _resolve_filename_filter passes
            # it through to this branch.)
            return _bad_request(
                f"invalid filename pattern {filename_pattern!r}"
            )
        # Mutex: centroid search cannot coexist with text prompts.
        if active_centroids and (prompt_state.q or prompt_state.positives or prompt_state.negatives):
            return _bad_request(
                "centroid search is exclusive — use ?centroid= or ?q=/?positives=, not both"
            )
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return _bad_request("limit must be an integer")
        if not (1 <= limit <= _cfg.top_k_max):
            return _bad_request(f"limit must be in [1, {_cfg.top_k_max}]")
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return _bad_request("offset must be an integer")
        if offset < 0:
            return _bad_request("offset must be >= 0")
        # Hard cap on total cumulative results served.
        if offset >= _cfg.max_results_total:
            return SearchResponse(
                query=prompt_state.q,
                positives=prompt_state.positives,
                negatives=prompt_state.negatives,
                diverse=diverse,
                diversity=DiversityMetadata(
                    requested=diverse,
                    applied=False,
                    mode=diversity_mode,
                    strength=diversity_strength,
                    depth=diversity_depth_mode,
                    pool_depth=0,
                ),
                view=view,
                centroid=active_centroid,
                centroids=list(active_centroids),
                weights=active_weights,
                results=[], took_ms=0, offset=offset, limit=0, has_more=False,
            )
        effective_limit = min(limit, _cfg.max_results_total - offset)

        collections = _parse_collections(request)

        if surprise and not prompt_state.positives and not active_centroids:
            # Surprise with no query: use zero vector so Qdrant
            # returns results from the whole collection.
            dim = _cfg.centroid_expected_feature_dim
            vec = [0.0] * dim
        else:
            vec, vec_err, vec_detail = _resolve_query_vector(
                active_centroids, prompt_state, weights=active_weights,
                filename_pattern=filename_pattern,
            )
            if vec_err == "centroid_not_found":
                return _bad_request(vec_detail or f"centroid {active_centroid!r} not loaded")
            if vec_err == "empty":
                return _bad_request(vec_detail or "at least one positive prompt is required")

        t0 = time.time()
        # `allowed_ids == []` short-circuit: skip the Qdrant round-trip
        # and return zero hits. See the matching block in `/` for why
        # we can't pass `has_id=[]` directly to HasIdCondition.
        if allowed_ids is not None and not allowed_ids:
            hits: list = []
            has_more = False
            favorite_ids = set()
            diversity_meta = DiversityMetadata(
                requested=diverse,
                applied=False,
                mode=diversity_mode,
                strength=diversity_strength,
                depth=diversity_depth_mode,
                pool_depth=0,
            )
        else:
            try:
                diversity_meta = DiversityMetadata()
                if diverse:
                    favorite_ids = await _favorite_ids_for_filter() if favorites else None
                    hits, has_more, diversity_meta = _diversity_page(
                        vec,
                        effective_limit,
                        offset,
                        collections,
                        allowed_ids,
                        favorite_ids,
                        diversity_mode,
                        diversity_strength,
                        diversity_depth_mode,
                        diversity_pool_depth,
                    )
                elif favorites:
                    favorite_ids = await _favorite_ids_for_filter()
                    hits_all, _ = qdrant.search(
                        vec, limit=_cfg.max_results_total, offset=0,
                        collections=collections or None,
                        allowed_ids=allowed_ids,
                    )
                    favorite_hits = [h for h in hits_all if h.id in favorite_ids]
                    hits = favorite_hits[offset:offset + effective_limit]
                    has_more = len(favorite_hits) > offset + effective_limit
                elif surprise:
                    pool = _cfg.surprise_pool_size
                    k = _cfg.surprise_result_count
                    hits, _ = qdrant.search(
                        vec, limit=pool, offset=0,
                        collections=collections or None,
                        allowed_ids=allowed_ids,
                    )
                    hits = _surprise_search(hits, k)
                    has_more = False
                else:
                    hits, has_more = qdrant.search(
                        vec, limit=effective_limit, offset=offset,
                        collections=collections or None,
                        allowed_ids=allowed_ids,
                    )
            except (ConnectionError, OSError) as e:
                logger.warning("Qdrant unreachable for /api/search: %s", e)
                return _qdrant_unreachable(str(e))
            except Exception as e:
                # Distinguish timeout-ish errors. qdrant-client raises
                # `qdrant_client.http.exceptions.TimeoutException` for timeouts.
                if "timeout" in type(e).__name__.lower() or "Timeout" in str(e):
                    logger.warning("Qdrant timeout: %s", e)
                    return _qdrant_timeout(str(e))
                logger.exception("search failed")
                return _internal_error(str(e))
        took_ms = int((time.time() - t0) * 1000)
        return SearchResponse(
            query=prompt_state.q,
            positives=prompt_state.positives,
            negatives=prompt_state.negatives,
            diverse=diverse,
            surprise=surprise,
            diversity=diversity_meta,
            view=view,
            centroid=active_centroid,
            centroids=list(active_centroids),
            weights=active_weights,
            results=await _results_from_hits(
                hits, favorite_ids if favorites else None
            ),
            took_ms=took_ms,
            offset=offset,
            limit=limit,
            has_more=has_more,
        )

    # ---------------------- Favourites ----------------------

    def _favorite_rows_to_results(rows: list[dict]) -> list[SearchResult]:
        def _maybe_int(v):
            try:
                iv = int(v) if v is not None else None
            except (TypeError, ValueError):
                return None
            return iv if iv and iv > 0 else None
        return [
            SearchResult(
                id=str(row["id"]),
                path=str(row["path"]),
                score=0.0,
                score_str="",
                url=resolve_url(str(row["id"]), _cfg.web_ui_url),
                is_favorite=True,
                width=_maybe_int(row.get("width")),
                height=_maybe_int(row.get("height")),
            )
            for row in rows
        ]

    def _album_member_rows_to_results(rows: list[dict]) -> list[SearchResult]:
        """Same shape as `_favorite_rows_to_results` but does NOT
        hardcode is_favorite=True — album membership is independent
        of favourites status, so the favourite state on the tile
        comes from the row's actual `is_favorite` column (left JOIN
        in IndexDB.get_by_id; album rows from list_album_members
        don't include it so default False is fine for v1).
        """
        # Lazy liveness: drop dead rows from album tiles. Same
        # defence as the random helper below.
        alive = [r for r in rows if _is_path_alive(str(r.get("path") or ""))]
        if len(alive) < len(rows):
            logger.debug(
                "album members: dropped %d dead row(s) from %d via lazy liveness",
                len(rows) - len(alive), len(rows),
            )
        rows = alive
        def _maybe_int(v):
            try:
                iv = int(v) if v is not None else None
            except (TypeError, ValueError):
                return None
            return iv if iv and iv > 0 else None
        return [
            SearchResult(
                id=str(row["id"]),
                path=str(row["path"]),
                score=0.0,
                score_str="",
                url=resolve_url(str(row["id"]), _cfg.web_ui_url),
                is_favorite=bool(int(row.get("is_favorite") or 0)),
                width=_maybe_int(row.get("width")),
                height=_maybe_int(row.get("height")),
            )
            for row in rows
        ]

    def _random_rows_to_results(rows: list[dict]) -> list[SearchResult]:
        """Build SearchResult objects from SQLite random-sample rows.

        Mirrors `_favorite_rows_to_results` but reads is_favorite from
        the cache (some random samples may already be favourites) and
        uses the cache's width/height for future masonry support.
        """
        # Lazy liveness: drop rows whose on-disk file is gone. The
        # IndexDB periodic refresh (force=True, every
        # index_db_refresh_interval_seconds) keeps the cache clean
        # in the long term; this is the always-on defense so /random
        # doesn't show broken tiles within the TTL window.
        alive = [r for r in rows if _is_path_alive(str(r.get("path") or ""))]
        if len(alive) < len(rows):
            logger.debug(
                "random: dropped %d dead row(s) from %d via lazy liveness",
                len(rows) - len(alive), len(rows),
            )
        rows = alive
        def _maybe_int(v):
            try:
                iv = int(v) if v is not None else None
            except (TypeError, ValueError):
                return None
            return iv if iv and iv > 0 else None
        out: list[SearchResult] = []
        for row in rows:
            is_fav = bool(int(row.get("is_favorite") or 0))
            out.append(
                SearchResult(
                    id=str(row["id"]),
                    path=str(row["path"]),
                    score=0.0,
                    score_str="",
                    url=resolve_url(str(row["id"]), _cfg.web_ui_url),
                    is_favorite=is_fav,
                    width=_maybe_int(row.get("width")),
                    height=_maybe_int(row.get("height")),
                )
            )
        return out

    @app.post("/api/favorites/{point_id}", response_model=FavoriteToggleResponse)
    async def mark_favorite(point_id: str) -> FavoriteToggleResponse:
        try:
            await asyncio.to_thread(index_db.mark_favorite, point_id)
        except ImageNotInCacheError as err:
            raise HTTPException(status_code=404, detail="Photo not found in index cache") from err
        # Invalidate the favourites dynamic centroid so the next
        # search through it reflects the new favourite.
        _invalidate_favourites_centroid()
        row = await asyncio.to_thread(index_db.get_by_id, point_id)
        return FavoriteToggleResponse(
            id=point_id,
            favorited_at=str((row or {}).get("favorited_at") or ""),
        )

    @app.delete("/api/favorites/{point_id}", status_code=204)
    async def unmark_favorite(point_id: str) -> None:
        row = await asyncio.to_thread(index_db.get_by_id, point_id)
        if row is None or int(row.get("is_favorite") or 0) != 1:
            raise HTTPException(status_code=404, detail="Favourite not found")
        await asyncio.to_thread(index_db.unmark_favorite, point_id)
        # Same invalidation as mark_favorite — every unmark moves the
        # centroid, and we don't try to detect whether it moved enough
        # to matter. Cheap, simple, correct.
        _invalidate_favourites_centroid()
        return None

    @app.get("/api/favorites")
    async def api_favorites(
        limit: int = Query(200, description="max favourites"),
        offset: int = Query(0, description="offset into favourites"),
        as_results: bool = Query(False, description="return SearchResponse-compatible shape"),
    ):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return _bad_request("limit must be an integer")
        if not (1 <= limit <= 1000):
            return _bad_request("limit must be in [1, 1000]")
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return _bad_request("offset must be an integer")
        if offset < 0:
            return _bad_request("offset must be >= 0")
        rows = await asyncio.to_thread(index_db.list_favorites, limit, offset)
        total = await asyncio.to_thread(index_db.count_favorites)
        if as_results:
            return SearchResponse(
                query="",
                positives=[],
                negatives=[],
                view=_cfg.default_view,
                centroid=None,
                results=_favorite_rows_to_results(rows),
                took_ms=0,
                offset=offset,
                limit=limit,
                has_more=offset + len(rows) < total,
            )
        return FavoritesListResponse(
            favorites=[
                {
                    "id": str(row["id"]),
                    "path": str(row["path"]),
                    "favorited_at": str(row["favorited_at"] or ""),
                }
                for row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    # ---------------------- Albums ----------------------
    #
    # User-curated collections of favourites. The favourites table
    # is the implicit default album (no row in `albums`); every
    # endpoint below is for user-created albums only. Album
    # membership is independent of favourites status: a photo can
    # be in an album without being favourited, and vice versa.
    #
    # Each album auto-registers as a dynamic centroid under the
    # name `album:{id}` so it can be used as a search primitive
    # via /api/search?centroid=album:42 (same shape as the
    # existing `favourites` centroid).

    @app.post("/api/albums", response_model=AlbumSummary)
    async def create_album(body: AlbumCreateRequest) -> AlbumSummary:
        try:
            album_id = await asyncio.to_thread(
                index_db.create_album, body.name, body.description,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        # Register the centroid so the album is immediately usable
        # as a search primitive (lazy compute — the first /api/search
        # call that uses it pays the cost).
        _register_album_centroid(album_id)
        albums = await asyncio.to_thread(index_db.list_albums)
        for a in albums:
            if a["id"] == album_id:
                return AlbumSummary(**a)
        # Shouldn't happen — we just inserted this row.
        raise HTTPException(status_code=500, detail="album not found after create")

    @app.get("/api/albums", response_model=AlbumsListResponse)
    async def list_albums() -> AlbumsListResponse:
        rows = await asyncio.to_thread(index_db.list_albums)
        return AlbumsListResponse(albums=[AlbumSummary(**r) for r in rows])

    @app.get("/api/albums/{album_id}", response_model=AlbumDetailResponse)
    async def get_album(
        album_id: int,
        limit: int = Query(200, description="max members to return"),
        offset: int = Query(0, description="offset into members"),
    ) -> AlbumDetailResponse:
        album = await asyncio.to_thread(index_db.get_album, album_id)
        if album is None:
            raise HTTPException(status_code=404, detail="Album not found")
        try:
            limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            limit = 200
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        rows = await asyncio.to_thread(
            index_db.list_album_members, album_id, limit, offset,
        )
        total = await asyncio.to_thread(
            index_db.count_album_members, album_id
        )
        return AlbumDetailResponse(
            id=album["id"],
            name=album["name"],
            description=album.get("description") or "",
            cover_favorite_id=album.get("cover_favorite_id") or "",
            created_at=album["created_at"],
            updated_at=album["updated_at"],
            members=[
                AlbumMemberItem(
                    id=str(row["id"]),
                    path=str(row["path"]),
                    added_at=str(row["added_at"] or ""),
                )
                for row in rows
            ],
            member_total=total,
        )

    @app.patch("/api/albums/{album_id}", response_model=AlbumSummary)
    async def update_album(
        album_id: int, body: AlbumUpdateRequest,
    ) -> AlbumSummary:
        # Build a rename tuple that's tolerant of partial updates
        # (only name, only description, or both). `rename_album`
        # requires both args or neither — we have to bridge the
        # partial case by reading the current row first.
        name = body.name
        description = body.description
        if name is None and description is None:
            raise HTTPException(
                status_code=400,
                detail="at least one of name or description is required",
            )
        if name is None:
            current = await asyncio.to_thread(index_db.get_album, album_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Album not found")
            name = current["name"]
        try:
            ok = await asyncio.to_thread(
                index_db.rename_album, album_id, name, description,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not ok:
            raise HTTPException(status_code=404, detail="Album not found")
        # Re-register so the centroid label picks up the new name.
        _register_album_centroid(album_id)
        for a in await asyncio.to_thread(index_db.list_albums):
            if a["id"] == album_id:
                return AlbumSummary(**a)
        raise HTTPException(status_code=500, detail="album not found after update")

    @app.delete("/api/albums/{album_id}", status_code=204)
    async def delete_album(album_id: int) -> None:
        ok = await asyncio.to_thread(index_db.delete_album, album_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Album not found")
        _unregister_album_centroid(album_id)

    @app.post(
        "/api/albums/{album_id}/members/{favorite_id}",
        response_model=AlbumMemberResponse,
    )
    async def add_album_member(
        album_id: int, favorite_id: str,
    ) -> AlbumMemberResponse:
        ok = await asyncio.to_thread(
            index_db.add_album_member, album_id, favorite_id,
        )
        if not ok:
            raise HTTPException(
                status_code=404,
                detail="Album not found or favourite already a member",
            )
        _invalidate_album_centroid(album_id)
        # Look up the membership row to return the canonical
        # added_at. The simpler approach would be to return a
        # computed "now" but that's inconsistent with re-adding
        # a removed favourite (where the added_at should be the
        # most recent add, not the original one).
        await asyncio.to_thread(
            index_db.list_album_member_ids, album_id,
        )
        # We need the added_at for this specific (album, favourite)
        # pair. The membership table isn't currently exposed by id
        # query, so fall back to the now-stored value by reading
        # list_album_members with a tight filter.
        rows = await asyncio.to_thread(
            index_db.list_album_members, album_id, 1, 0,
        )
        added_at = ""
        # list_album_members INNER JOINs against images, so an
        # orphan membership won't appear here. For a favourited
        # photo this is fine; for an orphan we'd need a separate
        # query path (not exposed yet — kept simple for v1).
        if rows and rows[0]["id"] == favorite_id:
            added_at = str(rows[0]["added_at"] or "")
        return AlbumMemberResponse(
            album_id=album_id,
            favorite_id=favorite_id,
            added_at=added_at,
        )

    @app.delete(
        "/api/albums/{album_id}/members/{favorite_id}",
        status_code=204,
    )
    async def remove_album_member(
        album_id: int, favorite_id: str,
    ) -> None:
        ok = await asyncio.to_thread(
            index_db.remove_album_member, album_id, favorite_id,
        )
        if not ok:
            raise HTTPException(
                status_code=404,
                detail="Album not found or favourite not a member",
            )
        _invalidate_album_centroid(album_id)

    @app.get(
        "/api/albums/by-favorite/{favorite_id}",
        response_model=AlbumMembershipsResponse,
    )
    async def list_albums_for_favorite(
        favorite_id: str,
    ) -> AlbumMembershipsResponse:
        """Return every album that contains `favorite_id`.

        Used by the per-photo UI to show which albums a photo is
        in. The summary shape omits member_count (always 1 for
        this view) so we re-use AlbumSummary with count=1.
        """
        rows = await asyncio.to_thread(
            index_db.list_albums_for_favorite, favorite_id,
        )
        summaries = [
            AlbumSummary(
                id=r["id"],
                name=r["name"],
                description=r.get("description") or "",
                cover_favorite_id="",
                member_count=1,
                created_at="",
                updated_at="",
            )
            for r in rows
        ]
        return AlbumMembershipsResponse(
            favorite_id=favorite_id,
            albums=summaries,
        )

    # ---------------------- Cache refresh ----------------------
    #
    # Two paths to refresh the IndexDB:
    #   1. Periodic background task (lifespan) — runs every
    #      INDEX_DB_REFRESH_INTERVAL_SECONDS (default 6h).
    #   2. Manual override via this endpoint — force-now button.
    # The periodic path respects the skip-if-populated branch in
    # `init_from_qdrant` (cheap when fresh); the manual path passes
    # `force=True` to bypass it.
    # A lock guards against the two paths racing — only one
    # refresh runs at a time; the other short-circuits with a
    # clear log message.

    @app.api_route("/api/cache/refresh", methods=["GET", "POST"])
    async def api_cache_refresh():
        # Cooperative refresh lock. If the periodic task is in the
        # middle of a refresh, the manual call bails immediately
        # rather than running two scrolls in parallel.
        if not index_db.try_acquire_refresh_lock():
            return {
                "status": "skipped",
                "reason": "refresh already in progress",
            }
        try:
            t0 = time.time()
            count = await asyncio.to_thread(
                index_db.init_from_qdrant, True
            )
            dt_ms = int((time.time() - t0) * 1000)
            logger.info("manual cache refresh: %d rows in %d ms", count, dt_ms)
            return {"status": "ok", "count": count, "took_ms": dt_ms}
        except (ConnectionError, OSError) as e:
            logger.warning("Qdrant unreachable for cache refresh: %s", e)
            return JSONResponse(
                status_code=502,
                content=ErrorResponse(
                    error="qdrant_unreachable",
                    detail=str(e),
                    code="qdrant_unreachable",
                ).model_dump(),
            )
        except Exception as e:
            logger.exception("cache refresh failed")
            return JSONResponse(
                status_code=500,
                content=ErrorResponse(
                    error="internal_error",
                    detail=str(e),
                    code="internal_error",
                ).model_dump(),
            )
        finally:
            index_db.release_refresh_lock()

    @app.get("/api/cache/status")
    async def cache_status() -> dict:
        """Operator visibility into the dual-store sync.

        Returns last refresh timestamp + duration, point counts in
        both stores, drift between them, the liveness cache size +
        cap, and the configured refresh interval / TTL. Drift is
        "unknown" when Qdrant is unreachable (qdrant_count == -1)
        so operators don't see a misleading negative number.
        """
        qdrant_count = index_db.qdrant_point_count()
        index_db_count = index_db.count_images()
        drift: int | str = (
            "unknown" if qdrant_count < 0 else qdrant_count - index_db_count
        )
        return {
            "last_refresh": index_db.last_refresh_time(),
            "last_refresh_duration_ms": index_db.last_refresh_duration_ms(),
            "qdrant_count": qdrant_count,
            "index_db_count": index_db_count,
            "drift": drift,
            "refresh_interval_seconds": _cfg.index_db_refresh_interval_seconds,
            "path_liveness_ttl_seconds": _cfg.path_liveness_ttl_seconds,
            "path_liveness_cache_size": len(_path_liveness_cache),
            "path_liveness_cache_max": _PATH_LIVENESS_CACHE_MAX,
        }

    @app.get("/favorites", response_class=HTMLResponse)
    async def favorites_page(
        request: Request,
        limit: int = Query(70, description="max favourites"),
        offset: int = Query(0, description="offset into favourites"),
        view: str = Query(_cfg.default_view, description="result view: 'grid' or 'feed'"),
    ) -> HTMLResponse:
        view = _coerce_view(view)
        try:
            limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            limit = 70
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        rows = await asyncio.to_thread(index_db.list_favorites, limit, offset)
        total = await asyncio.to_thread(index_db.count_favorites)
        results = _favorite_rows_to_results(rows)
        return templates.TemplateResponse(
            request,
            "favorites.html",
            {
                "results": results,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(results) < total,
                "max_results_total": total,
                "view": view,
                "q": "",
                "positives": [],
                "negatives": [],
                "search_query_string": "from_favorites=true",
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    # ---------------------- Albums HTML pages ----------------------
    #
    # Two routes: /albums (index with create form) and
    # /albums/{id} (detail with member grid + edit/delete).
    # Both reuse the same _result_grid.html partial the
    # favourites page uses, so the photo tile styling stays
    # consistent across all member-bearing surfaces.

    @app.get("/albums", response_class=HTMLResponse)
    async def albums_index(request: Request) -> HTMLResponse:
        rows = await asyncio.to_thread(index_db.list_albums)
        return templates.TemplateResponse(
            request,
            "albums.html",
            {
                "albums": rows,
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    # ---------------------- Saved searches ----------------------
    #
    # Named prompt presets. The user types a (positives, negatives)
    # combo into the search bar, names it, and the JSON shape is
    # stored verbatim. Applying a saved search in the UI just
    # re-populates the chip controllers — view, centroid,
    # favourites-filter and result limits are intentionally NOT
    # captured, because those are session state that wouldn't make
    # sense to recall across sessions (the photo set changes,
    # centroids get reloaded, view is whatever the device wants).

    @app.post("/api/saved-searches", response_model=SavedSearch, status_code=201)
    async def create_saved_search(body: SavedSearchCreateRequest) -> SavedSearch:
        # Name: trim, length-check. Empty / whitespace-only / >80
        # chars after strip → 400. The IndexDB also trims, but
        # validating here gives a precise error message and the
        # right status code.
        name = (body.name or "").strip()
        if not (1 <= len(name) <= 80):
            return _bad_request("name must be 1–80 characters after trim")
        # Prompts: strip and drop empty entries. At least one prompt
        # total across both lists must remain, otherwise the saved
        # search would be empty and useless.
        pos = [p.strip() for p in (body.positives or []) if isinstance(p, str) and p.strip()]
        neg = [p.strip() for p in (body.negatives or []) if isinstance(p, str) and p.strip()]
        if not pos and not neg:
            return _bad_request("at least one prompt is required in positives or negatives")
        try:
            row = await asyncio.to_thread(
                index_db.create_saved_search, name, pos, neg,
            )
        except ValueError as e:
            # UNIQUE-name conflict comes through as ValueError from
            # IndexDB. Surface as 409 with code=conflict so the UI
            # can show "name already in use, pick another" without
            # guessing the cause.
            return JSONResponse(
                status_code=409,
                content=ErrorResponse(
                    error="conflict", detail=str(e), code="conflict",
                ).model_dump(),
            )
        return SavedSearch(**row)

    @app.get("/api/saved-searches", response_model=SavedSearchListResponse)
    async def list_saved_searches(
        limit: int = Query(200, description="max saved searches"),
        offset: int = Query(0, description="offset into saved searches"),
    ) -> SavedSearchListResponse:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return _bad_request("limit must be an integer")
        if not (1 <= limit <= 1000):
            return _bad_request("limit must be in [1, 1000]")
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return _bad_request("offset must be an integer")
        if offset < 0:
            return _bad_request("offset must be >= 0")
        rows, total = await asyncio.to_thread(
            index_db.list_saved_searches, limit, offset,
        )
        return SavedSearchListResponse(
            saved_searches=[SavedSearch(**r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/saved-searches/{saved_id}", response_model=SavedSearch)
    async def get_saved_search(saved_id: int) -> SavedSearch:
        row = await asyncio.to_thread(index_db.get_saved_search, saved_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Saved search not found")
        return SavedSearch(**row)

    @app.delete("/api/saved-searches/{saved_id}", status_code=204)
    async def delete_saved_search(saved_id: int) -> None:
        ok = await asyncio.to_thread(index_db.delete_saved_search, saved_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Saved search not found")
        return None

    @app.get("/saved", response_class=HTMLResponse)
    async def saved_searches_index(request: Request) -> HTMLResponse:
        """Server-rendered list page for saved searches.

        Mostly a management view — delete a saved search by name.
        The primary entry point for *applying* a saved search is
        the dropdown on /  (the search bar), not this page. Kept
        simple: just a list with delete buttons, matching the
        shape of /favorites and /albums as "user state" landing
        pages.
        """
        rows, total = await asyncio.to_thread(
            index_db.list_saved_searches, 1000, 0,
        )
        return templates.TemplateResponse(
            request,
            "saved.html",
            {
                "saved_searches": rows,
                "total": total,
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    @app.get("/albums/{album_id}", response_class=HTMLResponse)
    async def album_detail_page(
        request: Request,
        album_id: int,
        limit: int = Query(70, description="max members to render"),
        offset: int = Query(0, description="offset into members"),
        view: str = Query(_cfg.default_view, description="result view: 'grid' or 'feed'"),
    ) -> HTMLResponse:
        view = _coerce_view(view)
        album = await asyncio.to_thread(index_db.get_album, album_id)
        if album is None:
            raise HTTPException(status_code=404, detail="Album not found")
        try:
            limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            limit = 70
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        rows = await asyncio.to_thread(
            index_db.list_album_members, album_id, limit, offset,
        )
        total = await asyncio.to_thread(
            index_db.count_album_members, album_id
        )
        results = _album_member_rows_to_results(rows)
        return templates.TemplateResponse(
            request,
            "album_detail.html",
            {
                "album": album,
                "results": results,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(results) < total,
                "max_results_total": total,
                "view": view,
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    # ---------------------- Favourites ZIP download ----------------------
    #
    # One-shot .zip of every favourite currently in the cache. Streamed
    # end-to-end so the response begins before every file has been
    # read into memory. Entry names are flattened to `{shard}__{basename}`
    # to avoid silent overwrites when two favourites share a basename
    # but live in different shards, and to avoid zip-path traversal
    # entirely. Files that can't be resolved on disk (deleted after
    # indexing, NAS unmounted) are skipped and recorded in a
    # `_missing.txt` manifest at the root of the archive.

    FAVORITES_ZIP_PAGE_SIZE = 500

    @app.api_route(
        "/favorites/download.zip", methods=["GET", "HEAD"],
        response_class=StreamingResponse,
    )
    async def favorites_download_zip() -> StreamingResponse:
        zs = zipstream.ZipStream(compress_type=zipstream.ZIP_STORED)

        # Favourites whose local file we couldn't resolve. Collected
        # here and emitted as `_missing.txt` at the root of the
        # archive so the user can see what was skipped.
        missing: list[tuple[str, str]] = []

        offset = 0
        while True:
            rows = index_db.list_favorites(
                limit=FAVORITES_ZIP_PAGE_SIZE,
                offset=offset,
            )
            if not rows:
                break
            for row in rows:
                point_id = str(row["id"])
                payload_path = str(row.get("path") or "")
                shard = str(row.get("shard") or "").strip()
                local = resolve_local(
                    payload_path,
                    _cfg.nas_images_base,
                    _cfg.path_prefix,
                )
                if local is None:
                    missing.append((point_id, payload_path))
                    continue
                # Flatten with shard prefix when known, otherwise
                # bare basename. We strip path separators from the
                # shard so no entry can ever traverse (``local.name``
                # is already just the file name via Path.name).
                safe_shard = (
                    shard.replace("/", "_").replace("\\", "_").strip("_")
                )
                entry_name = (
                    f"{safe_shard}__{local.name}" if safe_shard
                    else local.name
                )
                try:
                    # add_path streams the file in chunks as the
                    # consumer drains the response — no
                    # full-file buffering in RAM.
                    zs.add_path(local, arcname=entry_name)
                except (FileNotFoundError, OSError) as e:
                    logger.warning(
                        "favourites zip: failed to add %s for point %s: %s",
                        local, point_id, e,
                    )
                    missing.append((point_id, str(local)))
            offset += FAVORITES_ZIP_PAGE_SIZE

        if missing:
            lines = [
                "favourites zip manifest",
                f"missing files: {len(missing)}",
                "",
            ]
            for point_id, payload_path in missing:
                lines.append(f"{point_id}\t{payload_path}")
            zs.add("\n".join(lines) + "\n", arcname="_missing.txt")

        stamp = time.strftime("%Y-%m-%d")
        filename = f"favorites-{stamp}.zip"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        }
        return StreamingResponse(
            zs,
            media_type="application/zip",
            headers=headers,
        )

    @app.api_route(
        "/albums/{album_id}/download.zip", methods=["GET", "HEAD"],
        response_class=StreamingResponse,
    )
    async def album_download_zip(album_id: int) -> StreamingResponse:
        """Stream an album's photos as a zip — same shape and rules
        as `/favorites/download.zip`. Membership comes from
        `index_db.list_album_members` (INNER JOIN against the cache,
        so orphan rows are hidden from the archive too). Files we
        can't resolve on disk are skipped and recorded in
        `_missing.txt`. Album id with no row → 404.
        """
        album = index_db.get_album(album_id)
        if album is None:
            raise HTTPException(
                status_code=404, detail=f"Album {album_id} not found",
            )

        zs = zipstream.ZipStream(compress_type=zipstream.ZIP_STORED)
        missing: list[tuple[str, str]] = []

        offset = 0
        while True:
            rows = index_db.list_album_members(
                album_id,
                limit=FAVORITES_ZIP_PAGE_SIZE,
                offset=offset,
            )
            if not rows:
                break
            for row in rows:
                point_id = str(row["id"])
                payload_path = str(row.get("path") or "")
                shard = str(row.get("shard") or "").strip()
                local = resolve_local(
                    payload_path,
                    _cfg.nas_images_base,
                    _cfg.path_prefix,
                )
                if local is None:
                    missing.append((point_id, payload_path))
                    continue
                safe_shard = (
                    shard.replace("/", "_").replace("\\", "_").strip("_")
                )
                entry_name = (
                    f"{safe_shard}__{local.name}" if safe_shard
                    else local.name
                )
                try:
                    zs.add_path(local, arcname=entry_name)
                except (FileNotFoundError, OSError) as e:
                    logger.warning(
                        "album zip: failed to add %s for point %s: %s",
                        local, point_id, e,
                    )
                    missing.append((point_id, str(local)))
            offset += FAVORITES_ZIP_PAGE_SIZE

        if missing:
            lines = [
                f"album {album_id} zip manifest",
                f"missing files: {len(missing)}",
                "",
            ]
            for point_id, payload_path in missing:
                lines.append(f"{point_id}\t{payload_path}")
            zs.add("\n".join(lines) + "\n", arcname="_missing.txt")

        stamp = time.strftime("%Y-%m-%d")
        # Slug the album name for the filename. Empty / unsafe names
        # fall back to the bare id (the prefix below adds "album-").
        # Cap length so the filename stays well under common
        # filesystem limits even after concatenation.
        raw = re.sub(r"[^A-Za-z0-9._-]+", "-", album["name"]).strip("-")
        slug = raw[:64] if raw else str(album_id)
        filename = f"album-{slug}-{stamp}.zip"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        }
        return StreamingResponse(
            zs,
            media_type="application/zip",
            headers=headers,
        )

    # ---------------------- Random shuffle ----------------------
    #
    # /random surfaces a fresh batch of photos from the index cache
    # without going through Qdrant. The cache is the source of truth
    # for random sampling — every photo there is already indexed, so
    # the cache has id/path/dimensions, which is everything the page
    # needs. A "shuffle" button just hits the API again and re-renders.
    #
    # Collection filter is a list (multi-value query param), so the
    # UI can offer chips; passing no filter means "from the whole set".

    RANDOM_DEFAULT_LIMIT = 70
    RANDOM_MAX_LIMIT = 200

    # Number of random photos shown on the default landing page
    # (when there's no query and no centroid). Lightweight — just a
    # visual nudge toward the collection when nothing else is asked
    # for. Same SQLite sample path as /api/random.
    HOME_RANDOM_PICKS = 28

    @app.get("/api/random", response_model=SearchResponse)
    async def api_random(
        request: Request,
        limit: int = Query(RANDOM_DEFAULT_LIMIT, description="max results"),
        collections: Annotated[list[str], Query(description="restrict to one or more collections; empty = whole set")] = [],  # noqa: B006
        view: str = Query(_cfg.default_view, description="result view: 'grid' or 'feed'"),
    ) -> SearchResponse:
        view = _coerce_view(view)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return _bad_request("limit must be an integer")
        if not (1 <= limit <= RANDOM_MAX_LIMIT):
            return _bad_request(f"limit must be in [1, {RANDOM_MAX_LIMIT}]")
        # Clean up the collection list (drop empties, dedupe while
        # preserving order so the response is stable for the client).
        seen: set[str] = set()
        clean_collections: list[str] = []
        for c in collections:
            c = (c or "").strip()
            if c and c not in seen:
                seen.add(c)
                clean_collections.append(c)
        try:
            rows = await asyncio.to_thread(
                index_db.pick_random_rows, limit, clean_collections or None
            )
        except Exception as e:
            logger.exception("random sample failed")
            return _internal_error(str(e))
        results = _random_rows_to_results(rows)
        # has_more = True when we filled the page (might be more) or
        # when the caller asked for more than the collection holds
        # (everything fits, nothing more). The /random UI uses an
        # IntersectionObserver to append on scroll; the sentinel stays
        # until a fetch returns fewer than `limit` rows, signalling
        # "collection exhausted, stop scrolling".
        has_more = len(results) >= limit
        return SearchResponse(
            query="",
            positives=[],
            negatives=[],
            view=view,
            centroid=None,
            results=results,
            took_ms=0,
            offset=0,
            limit=limit,
            has_more=has_more,
        )

    @app.get("/random", response_class=HTMLResponse)
    async def random_page(
        request: Request,
        limit: int = Query(RANDOM_DEFAULT_LIMIT, description="max results"),
        collections: Annotated[list[str], Query(description="restrict to one or more collections; empty = whole set")] = [],  # noqa: B006
        view: str = Query(_cfg.default_view, description="result view: 'grid' or 'feed'"),
    ) -> HTMLResponse:
        view = _coerce_view(view)
        try:
            limit = max(1, min(int(limit), RANDOM_MAX_LIMIT))
        except (TypeError, ValueError):
            limit = RANDOM_DEFAULT_LIMIT
        # Clean collections the same way the API does.
        seen: set[str] = set()
        clean_collections: list[str] = []
        for c in collections:
            c = (c or "").strip()
            if c and c not in seen:
                seen.add(c)
                clean_collections.append(c)
        try:
            rows = await asyncio.to_thread(
                index_db.pick_random_rows, limit, clean_collections or None
            )
        except Exception:
            logger.exception("random page render failed")
            return templates.TemplateResponse(
                request,
                "random.html",
                {
                    "results": [],
                    "view": view,
                    "collections": clean_collections,
                    "limit": limit,
                    "error": "Could not sample photos right now.",
                    "static_assets_version": _cfg.static_assets_version,
                },
                status_code=500,
            )
        results = _random_rows_to_results(rows)
        # JSON-serialisable dicts for the SSR data block. SearchResult
        # is a pydantic model; Jinja's tojson filter can't serialise
        # it directly. List of dicts is what `_result_grid.html`
        # consumes too (it only reads attributes).
        results_dicts = [r.model_dump() for r in results]
        return templates.TemplateResponse(
            request,
            "random.html",
            {
                "results": results_dicts,
                "view": view,
                "collections": clean_collections,
                "limit": limit,
                "error": None,
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    # ---------------------- Collections endpoint ----------------------

    @app.get("/api/collections")
    async def list_collections():
        """
        Return a list of distinct library (`collection` payload field)
        values with point counts. Drives the chip-style filter UI on
        the frontend; one call per page load.
        """
        try:
            return {"collections": qdrant.list_collections_with_counts()}
        except (ConnectionError, OSError) as e:
            logger.warning("Qdrant unreachable for /api/collections: %s", e)
            return JSONResponse(
                status_code=502,
                content=ErrorResponse(
                    error="qdrant_unreachable",
                    detail=str(e),
                    code="qdrant_unreachable",
                ).model_dump(),
            )

    # ---------------------- Centroids ----------------------
    #
    # Custom centroid vectors: read-only .pt files written by
    # `isaac-image-scoring`. The store is built once at startup;
    # `POST /api/centroids/reload` is the only way to refresh it
    # (no filesystem watcher, by design — keeps the search side
    # boring). Routes:
    #   GET  /api/centroids                  — JSON list of loaded centroids
    #   GET  /api/centroids/{name}/search    — search using centroid as anchor
    #   POST /api/centroids/reload           — rescan CENTROIDS_DIR
    #
    # The HTML list page (`GET /centroids`) lives in commit 3; the
    # route stub is registered here as a no-op-friendly placeholder
    # so the URL is owned before the template exists.

    @app.get("/api/centroids")
    async def list_centroids() -> dict:
        """
        List all centroids currently loaded from CENTROIDS_DIR.
        Each entry includes model/dim metadata so the UI can show
        "expected vs loaded" mismatches if a future debug view needs it.

        Dynamic centroids (runtime-computed, currently just
        `favourites`) are returned alongside the static ones in a
        separate `dynamic_centroids` list so the UI can render them
        in their own section.
        """
        if _centroid_store is None:
            static: list = []
            expected_model = None
            expected_feature_dim = None
        else:
            static = [c.as_dict() for c in _centroid_store.list()]
            expected_model = _centroid_store.expected_model()
            expected_feature_dim = _centroid_store.expected_feature_dim()
        dynamic = []
        if _dynamic_centroids is not None:
            for spec in _dynamic_centroids.list():
                # Trigger a compute (cached) so the API response
                # includes the real n_images count rather than None.
                _dynamic_centroids.get_vector(spec.name)
                dynamic.append(
                    spec.public_dict(_dynamic_centroids.cached_n_images(spec.name))
                )
        return {
            "centroids": static,
            "dynamic_centroids": dynamic,
            "expected_model": expected_model,
            "expected_feature_dim": expected_feature_dim,
        }

    @app.get("/api/centroids/{name}/search", response_model=SearchResponse)
    async def search_by_centroid(
        name: str,
        request: Request,
        limit: int = Query(_cfg.top_k_default, description="max results"),
        offset: int = Query(0, description="offset into the full result set"),
    ):
        """
        Search using a loaded centroid as the query vector. Mutually
        exclusive with text prompts (the URL shape carries no prompt
        params, so the only failure mode is an unknown centroid name).
        """
        if _centroid_store is None:
            raise HTTPException(status_code=503, detail="centroid store not initialized")
        # Look up static first; fall back to dynamic (registry does
        # lazy compute + cache). This keeps the route's contract the
        # same regardless of which backend the centroid came from.
        # `centroid_name` echoes the canonical form back to the client
        # (static centroids are stored lowercased, dynamic use the
        # registered name as-is).
        # `seed_ids` is the list of source point ids that fed the
        # centroid (favourite ids / album member ids). Empty for
        # static `.pt` centroids. Drives the two-layer near-dup
        # exclusion below: Layer 1 is the `exclude_ids` server-side
        # filter; Layer 2 is the numpy post-pass on candidate
        # vectors. Both no-op when `seed_ids` is empty (the static
        # case).
        vector: list[float] | None = None
        centroid_name = name
        seed_ids: list[str] = []
        static_spec = _centroid_store.get(name)
        if static_spec is not None:
            vector = static_spec.vector
            centroid_name = static_spec.name
        elif _dynamic_centroids is not None:
            dyn = _dynamic_centroids.get_vector(name)
            dyn_spec = _dynamic_centroids.get_spec(name)
            if dyn is not None:
                vector, _, seed_ids = dyn
                if dyn_spec is not None:
                    centroid_name = dyn_spec.name
            else:
                # Distinguish "unknown name" from "known but empty":
                # the empty case surfaces a 404 too (treated as
                # "nothing to search against") so the UI can show its
                # own empty-state copy. Names not registered are
                # caught below.
                if _dynamic_centroids.get_spec(name) is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"centroid {name!r} not loaded",
                    )
                raise HTTPException(
                    status_code=404,
                    detail=f"centroid {name!r} has no data yet",
                )
        else:
            raise HTTPException(
                status_code=404, detail=f"centroid {name!r} not loaded"
            )
        if vector is None:
            raise HTTPException(
                status_code=404, detail=f"centroid {name!r} not loaded"
            )
        # Manual validation (consistent with /api/search shape).
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return _bad_request("limit must be an integer")
        if not (1 <= limit <= _cfg.top_k_max):
            return _bad_request(f"limit must be in [1, {_cfg.top_k_max}]")
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return _bad_request("offset must be an integer")
        if offset < 0:
            return _bad_request("offset must be >= 0")
        if offset >= _cfg.max_results_total:
            return SearchResponse(
                query="",
                positives=[],
                negatives=[],
                view=_cfg.default_view,
                centroid=centroid_name,
                results=[], took_ms=0, offset=offset, limit=0, has_more=False,
            )
        effective_limit = min(limit, _cfg.max_results_total - offset)
        collections = _parse_collections(request)
        filename_pattern = _parse_filename(request)
        allowed_ids, fname_err = await _resolve_filename_filter(
            filename_pattern
        )
        if fname_err == "bad_request":
            return _bad_request(
                f"invalid filename pattern {filename_pattern!r}"
            )

        t0 = time.time()
        # `allowed_ids == []` short-circuit (see `/` and `/api/search`
        # for the rationale).
        if allowed_ids is not None and not allowed_ids:
            hits: list = []
            has_more = False
        else:
            # Two-layer near-duplicate exclusion when we have a
            # dynamic centroid (seed_ids is non-empty).
            #
            # Layer 1 — exact-id `must_not` at Qdrant (cheap): kills
            #   exact-seed matches at the filter level so the
            #   over-fetch doesn't waste bandwidth on results we'd
            #   drop on the Python side.
            #
            # Layer 2 — numpy post-pass (the real ask): for each
            #   candidate hit we compute its cosine distance to the
            #   NEAREST seed vector and drop hits tighter than a
            #   threshold calibrated from the seed set's OWN
            #   intra-cluster pairwise distances (the
            #   "how-close-do-two-versions-of-the-same-photo-get"
            #   scale for THIS centroid). Threshold = 1st-percentile
            #   of intra-seed pairwise cosine distances — a
            #   conservative cutoff that only drops things tighter
            #   than the tightest typical seed-seed pair.
            #
            # Over-fetch: ask Qdrant for `effective_limit * 3`
            #   candidates (capped at _cfg.max_results_total) so that
            #   after Layer 2 drops near-dups we still have enough
            #   to trim back to `effective_limit`. When the seed set
            #   is tight (typical for albums), the 3x headroom is
            #   usually enough; for an extreme case where > 2/3 of
            #   the top results are near-dups of the seeds we'd
            #   under-fill the page. That's accepted: the user
            #   already has <effective_limit distinct results, so
            #   the alternative (refetching with a larger limit
            #   until we have enough) introduces a separate failure
            #   mode for a rare edge.
            #
            # `has_more` policy post-Layer-2: if anything was
            #   dropped, we say `has_more=True` even if we already
            #   trimmed to `effective_limit` — there may be more
            #   distinct results beyond the offset. If nothing was
            #   dropped, fall through to the regular
            #   "result_count == limit" heuristic.
            over_fetch_limit = min(
                effective_limit * 3, _cfg.max_results_total - offset
            )
            try:
                if seed_ids:
                    # Need vectors for the Layer 2 post-pass, so go
                    # through `search_with_vectors` (one extra
                    # `with_vectors=True` per hit, no second
                    # round-trip). The same `exclude_ids` Layer 1
                    # filter rides along.
                    pairs, _ = qdrant.search_with_vectors(
                        vector, limit=over_fetch_limit, offset=offset,
                        collections=collections or None,
                        allowed_ids=allowed_ids,
                        exclude_ids=seed_ids,
                    )
                    # Fetch the seed vectors themselves for the
                    # calibration + post-pass. Orphans (ids whose
                    # photo is gone from Qdrant) are silently
                    # dropped here — `retrieve_batch_with_vectors`
                    # omits missing ids from the response.
                    seed_pairs = qdrant.retrieve_batch_with_vectors(seed_ids)
                    seed_vecs: list[list[float]] = [v for _, v in seed_pairs]
                    if seed_vecs:
                        threshold = calibrate_near_dup_threshold(seed_vecs)
                        cand_vecs = [vec for _, vec in pairs]
                        keep_mask = filter_near_duplicates(
                            cand_vecs, seed_vecs, threshold,
                        )
                        before_count = len(pairs)
                        kept_pairs = [
                            p for p, keep in zip(pairs, keep_mask, strict=False) if keep
                        ]
                        dropped = before_count - len(kept_pairs)
                        # Trim back to what the user asked for.
                        hits = [h for h, _ in kept_pairs[:effective_limit]]
                        # If anything was dropped, signal `has_more`
                        # so the user knows there might be more
                        # distinct results if they paginate. Also
                        # if we filled the limit and there were more
                        # candidates kept than what we returned.
                        if dropped > 0 or len(kept_pairs) > effective_limit:
                            has_more = True
                        else:
                            # Nothing dropped AND we filled the page
                            # — but Qdrant only gave us `before_count`
                            # candidates, not `over_fetch_limit`, so
                            # we can't use the over-fetched limit
                            # for the standard "hit the limit means
                            # more" heuristic. Use the user's
                            # `effective_limit`: if we filled it,
                            # there may be more; if we didn't, there
                            # isn't.
                            has_more = len(hits) >= effective_limit
                    else:
                        # Seed vectors weren't retrievable (all
                        # orphans) — skip Layer 2 and just trim.
                        # Don't apply exclude_ids post-hoc because
                        # Layer 1 already excluded them server-side.
                        hits = [h for h, _ in pairs[:effective_limit]]
                        has_more = (
                            len(pairs) > effective_limit
                            or len(pairs) >= effective_limit
                        )
                else:
                    # Static centroid (or empty dynamic): no
                    # near-dup exclusion. Original single-shot
                    # search path.
                    hits, has_more = qdrant.search(
                        vector, limit=effective_limit, offset=offset,
                        collections=collections or None,
                        allowed_ids=allowed_ids,
                    )
            except (ConnectionError, OSError) as e:
                logger.warning("Qdrant unreachable for centroid search: %s", e)
                return _qdrant_unreachable(str(e))
            except Exception as e:
                if "timeout" in type(e).__name__.lower() or "Timeout" in str(e):
                    logger.warning("Qdrant timeout: %s", e)
                    return _qdrant_timeout(str(e))
                logger.exception("centroid search failed")
                return _internal_error(str(e))
        took_ms = int((time.time() - t0) * 1000)
        return SearchResponse(
            query="",
            positives=[],
            negatives=[],
            view=_cfg.default_view,
            centroid=centroid_name,
            results=[
                SearchResult(
                    id=h.id, path=h.path, score=h.score,
                    score_str=f"{h.score:.3f}",
                    url=resolve_url(h.id, _cfg.web_ui_url),
                )
                for h in hits
            ],
            took_ms=took_ms,
            offset=offset,
            limit=limit,
            has_more=has_more,
        )

    @app.post("/api/centroids/reload")
    async def reload_centroids() -> dict:
        """
        Rescan CENTROIDS_DIR and rebuild the in-memory store.

        Manual on purpose — the search side has no filesystem watcher.
        The response includes the new count and the directory that
        was scanned, so the caller can confirm what was reloaded.
        """
        if _centroid_store is None:
            raise HTTPException(status_code=503, detail="centroid store not initialized")
        count = _centroid_store.load()
        return {
            "count": count,
            "centroids_dir": str(_centroid_store.centroids_dir) if _centroid_store.centroids_dir else None,
        }

    @app.get("/centroids", response_class=HTMLResponse)
    async def centroids_page(request: Request) -> HTMLResponse:
        """
        List page for loaded centroids. Each row is a card with
        the centroid's name, model/dim, source image count, and
        a link that drops the user into the search results with
        that centroid as the anchor.

        The page is read-only — there is no edit/create path on
        the search side. Centroids are owned by `isaac-image-scoring`.
        A "reload" button calls POST /api/centroids/reload to
        rescan the directory without restarting the container.
        """
        if _centroid_store is None:
            centroids: list = []
            dir_label = None
        else:
            centroids = [c.as_dict() for c in _centroid_store.list()]
            dir_label = str(_centroid_store.centroids_dir) if _centroid_store.centroids_dir else None
        # Dynamic centroids: include each spec + its empty-state flag
        # + the cached n_images (None if never computed) so the
        # template can show a friendly hint when compute returned None.
        dynamic = []
        if _dynamic_centroids is not None:
            for spec in _dynamic_centroids.list():
                # Trigger a compute (cached) so the page can render
                # the empty-state hint or the live n_images count
                # rather than the "not yet computed" state. Cheap:
                # one Qdrant retrieve + numpy mean + L2 norm.
                _dynamic_centroids.get_vector(spec.name)
                dynamic.append({
                    "name": spec.name,
                    "label": spec.label,
                    "description": spec.description,
                    "source": spec.source,
                    "empty_message": spec.empty_message,
                    "n_images": _dynamic_centroids.cached_n_images(spec.name),
                    "is_empty": _dynamic_centroids.is_empty(spec.name),
                })
        return templates.TemplateResponse(
            request,
            "centroids.html",
            {
                "centroids": centroids,
                "centroids_dir": dir_label,
                "dynamic_centroids": dynamic,
                # Pre-check the multi-select checkboxes for whatever
                # centroids are already active in the URL. Lets users
                # land on /centroids from a blended search and add a
                # third centroid without re-picking the first two.
                "preselected_centroids": _parse_centroids(request),
                "expected_model": _cfg.centroid_expected_model if _cfg else None,
                "expected_feature_dim": _cfg.centroid_expected_feature_dim if _cfg else None,
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    # ---------------------- Discovery rabbithole ----------------------
    #
    # Read-only, ephemeral two-image pick flow. Qdrant never gets
    # written to; session state is in-memory only (lost on server
    # restart). The user is the only one who can end the session.
    #
    # Routes:
    #   GET  /discover               — render the empty page (JS hydrates)
    #   POST /api/discover/start     — create session, return first pair
    #   POST /api/discover/pick      — submit a pick, return next pair
    #   GET  /discover/liked         — render the gallery of picks

    @app.get("/discover", response_class=HTMLResponse)
    async def discover_page(request: Request) -> HTMLResponse:
        """Render the discovery page. The JS controller hydrates
        the first pair via /api/discover/start."""
        return templates.TemplateResponse(
            request,
            "discover.html",
            {
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    def _hydrate_pair_urls(pair: DiscoveryPair | None) -> DiscoveryPair | None:
        """Fill in the public /photo/{id}/raw URL on each image.

        discover.py builds pairs with empty URLs because it doesn't
        know the web_ui_url. We patch them in here, where the
        config is available.
        """
        if pair is None:
            return None
        if pair.left is not None and not pair.left.url:
            pair.left.url = resolve_url(pair.left.id, _cfg.web_ui_url)
        if pair.right is not None and not pair.right.url:
            pair.right.url = resolve_url(pair.right.id, _cfg.web_ui_url)
        return pair

    @app.post("/api/discover/start", response_model=DiscoveryStartResponse)
    async def discover_start() -> DiscoveryStartResponse:
        """Create a new discovery session and return the first pair."""
        try:
            session_id, pair = discover.start_session(
                qdrant, discover.DiscoverOptions.from_config(_cfg), index_db,
            )
        except (ConnectionError, OSError) as e:
            logger.warning("Qdrant unreachable for /api/discover/start: %s", e)
            raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
        return DiscoveryStartResponse(
            session_id=session_id,
            pair=_hydrate_pair_urls(pair),  # type: ignore[arg-type]
        )

    @app.post("/api/discover/pick", response_model=DiscoveryPickResponse)
    async def discover_pick(
        session_id: str = Query(..., description="discovery session id"),
        image_id: str = Query(..., description="the image id the user picked"),
    ) -> DiscoveryPickResponse:
        """Record a pick and return the next pair.

        Returns pair=None if the session is gone (expired TTL,
        server restart, fake id). The frontend treats that as
        "session ended, start over" and redirects to /discover.
        """
        try:
            next_pair = discover.submit_pick(
                qdrant, session_id, image_id,
                discover.DiscoverOptions.from_config(_cfg), index_db,
            )
        except (ConnectionError, OSError) as e:
            logger.warning("Qdrant unreachable for /api/discover/pick: %s", e)
            raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
        session = discover.get_session(session_id)
        liked_count = len(session.liked) if session else 0
        round_completed = session.round if session else 0
        return DiscoveryPickResponse(
            pair=_hydrate_pair_urls(next_pair),
            round=round_completed,
            liked_count=liked_count,
        )

    @app.get("/discover/liked", response_class=HTMLResponse)
    async def discover_liked_page(
        request: Request,
        session_id: str = Query(..., description="discovery session id"),
        view: str = Query(_cfg.default_view, description="result view: 'grid' (default) or 'feed'"),
    ) -> HTMLResponse:
        """Render the gallery of images the user picked in this session."""
        view = _coerce_view(view)
        images = discover.list_liked(qdrant, session_id, _cfg.web_ui_url, index_db)
        if images is None:
            # Session gone. Render a friendly empty state with a
            # link back to /discover.
            return templates.TemplateResponse(
                request,
                "discover_liked.html",
                {
                    "session_id": session_id,
                    "view": view,
                    "images": [],
                    "session_gone": True,
                    "static_assets_version": _cfg.static_assets_version,
                },
            )
        return templates.TemplateResponse(
            request,
            "discover_liked.html",
            {
                "session_id": session_id,
                "view": view,
                "images": images,
                "session_gone": False,
                "static_assets_version": _cfg.static_assets_version,
            },
        )

    # ---------------------- Error response helpers ----------------------

    def _bad_request(detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="bad_request", detail=detail, code="bad_request").model_dump(),
        )

    def _qdrant_unreachable(detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(error="qdrant_unreachable", detail=detail, code="qdrant_unreachable").model_dump(),
        )

    def _qdrant_timeout(detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=504,
            content=ErrorResponse(error="qdrant_timeout", detail=detail, code="qdrant_timeout").model_dump(),
        )

    def _internal_error(detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(error="internal_error", detail=detail, code="internal_error").model_dump(),
        )

    @app.get("/healthz")
    async def healthz() -> dict:
        ok = qdrant.healthz()
        return {"qdrant": ok, "test_mode": _cfg.test_mode}

    return app


# Allow `python -m search.app` to run a dev server.
app: FastAPI | None = None


def _build_default_app() -> FastAPI:
    return create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("search.app:_build_default_app", factory=True, host="0.0.0.0", port=8000)  # noqa: S104
