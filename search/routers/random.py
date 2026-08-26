"""
search/routers/random.py — /api/random (§B2 step 9).

GET /api/random?session=&offset=&limit=&collections=&view=:
    Walk through the library in a per-session shuffled order. The
    first call materializes a shuffled deck (random permutation of
    all point ids matching the collection filter) and returns the
    first `limit` photos. Subsequent calls pass the same `session`
    id and an `offset` to walk forward through the deck.

    Why session + offset (not "give me 20 random, dedupe client-side"):
    - Dedupe-on-client gets exponentially less productive as the
      on-screen set grows. The right shape is a server-side cursor
      that guarantees every photo is served exactly once per session.
    - One shuffle per session, O(N) where N = collection size.
      At 182 photos this is microseconds; at 2M it's a one-time
      ~5s query, then O(1) per request.

Two non-obvious things the route does:

1. **Session TTL** — sessions live 30 minutes by default
   (RANDOM_SESSION_TTL_S). After that the shuffled deck is
   discarded and the next call gets a fresh shuffle. Configurable
   for tests.

2. **No liveness filter** — the random route does NOT drop
   dead-NAS-file rows. The /random UX tolerates a broken tile for
   one cache refresh (60 s); filtering would require a second DB
   pass per request.

Tests pin:
- The endpoint returns the documented SearchResponse shape.
- Manual validation of `limit` and `offset` return 400 (not 422).
- First call materializes a session and returns `session_id`.
- Subsequent calls with the same session_id walk forward.
- Session expires after TTL.
- Two concurrent sessions don't see the same deck.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from search.image_resolver import resolve_url
from search.models import ErrorResponse, SearchResponse, SearchResult

logger = logging.getLogger(__name__)


# Match the inline limit that was in `app.py`. Kept as a module
# constant so callers / tests / docs can reference one source of truth.
RANDOM_MAX_LIMIT = 200

# Default session lifetime. After this many seconds the shuffled
# deck is discarded and the next call gets a fresh shuffle.
RANDOM_SESSION_TTL_S = 30 * 60


def _random_rows_to_results(
    rows: list[dict], *, web_ui_url: str,
) -> list[SearchResult]:
    """Build SearchResult objects from SQLite random-sample rows.

    Mirrors `_favorite_rows_to_results` but reads is_favorite from
    the cache and uses the cache's width/height for future masonry
    support. No liveness filter — see module docstring.
    """

    def _maybe_int(v: Any) -> int | None:
        try:
            iv = int(v) if v is not None else None
        except (TypeError, ValueError):
            return None
        return iv if iv and iv > 0 else None

    out: list[SearchResult] = []
    for row in rows:
        is_fav = bool(int(row.get("is_favorite") or 0))
        _bh = row.get("blurhash") or None
        out.append(
            SearchResult(
                id=str(row["id"]),
                path=str(row["path"]),
                score=0.0,
                score_str="",
                url=resolve_url(str(row["id"]), web_ui_url),
                is_favorite=is_fav,
                is_disliked=bool(int(row.get("is_disliked") or 0)),
                width=_maybe_int(row.get("width")),
                height=_maybe_int(row.get("height")),
                blurhash=_bh,
            )
        )
    return out


def _coerce_view(raw: str | None) -> str:
    """Coerce view param to a known value. Defaults to 'grid'."""
    if raw in ("grid", "feed"):
        return raw
    return "grid"


# ---------------------------------------------------------------------------
# Session store — in-memory, per-process. One shuffled deck per session id.
# ---------------------------------------------------------------------------


class _RandomSession:
    """One shuffled walk through the library."""

    __slots__ = ("ids", "created_at", "ttl_s")

    def __init__(self, ids: list[str], ttl_s: float):
        self.ids = ids
        self.created_at = time.monotonic()
        self.ttl_s = ttl_s

    def is_alive(self) -> bool:
        return (time.monotonic() - self.created_at) < self.ttl_s


class _RandomSessionStore:
    """Process-local map of session_id → shuffled deck.

    Deliberately NOT shared across processes — a shuffle is cheap to
    rebuild, and cross-process state would mean sticky sessions on a
    load balancer, which is the opposite of what /random wants.
    """

    def __init__(self, ttl_s: float = RANDOM_SESSION_TTL_S):
        self._sessions: dict[str, _RandomSession] = {}
        self._ttl_s = ttl_s

    def get(
        self,
        session_id: str | None,
    ) -> tuple[str, _RandomSession] | None:
        """Look up a live session by id. Returns None if the id is
        missing, or if the session has expired (in which case the
        entry is removed).

        Splitting lookup from creation lets the caller run
        materialize off the event loop (asyncio.to_thread) without
        awkward sync/async interleaving in the store.
        """
        if not session_id:
            return None
        sess = self._sessions.get(session_id)
        if sess is None:
            return None
        if not sess.is_alive():
            del self._sessions[session_id]
            return None
        return session_id, sess

    def put_new(self, ids: list[str]) -> tuple[str, _RandomSession]:
        """Materialize a fresh session with a new id and the given
        shuffled deck. The caller is responsible for having run the
        materialize (possibly in a thread).
        """
        new_id = secrets.token_urlsafe(16)
        sess = _RandomSession(ids, self._ttl_s)
        self._sessions[new_id] = sess
        return new_id, sess

    def clear(self) -> None:
        """Drop all sessions. Used by tests."""
        self._sessions.clear()

    def __len__(self) -> int:
        return len(self._sessions)


def build_random_router(
    index_db: Any,
    cfg: Any,
) -> APIRouter:
    """Build the random router with the live dependencies."""
    router = APIRouter()
    store = _RandomSessionStore()

    @router.get("/api/random", response_model=SearchResponse)
    async def api_random(
        request: Request,  # kept for parity with /api/search
        limit: int = Query(cfg.top_k_default, description="max results"),
        offset: int = Query(0, description="position in the shuffled deck"),
        session: Annotated[
            str | None,
            Query(description="session id from a previous /api/random response"),
        ] = None,
        collections: Annotated[
            list[str] | None,
            Query(description="restrict to one or more collections; empty = whole set"),
        ] = None,
        view: str = Query(cfg.default_view, description="result view: 'grid' or 'feed'"),
    ) -> SearchResponse:
        view = _coerce_view(view)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return _bad_request("limit must be an integer")  # type: ignore[return-value]
        if not (1 <= limit <= RANDOM_MAX_LIMIT):
            return _bad_request(f"limit must be in [1, {RANDOM_MAX_LIMIT}]")  # type: ignore[return-value]
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return _bad_request("offset must be an integer")  # type: ignore[return-value]
        if offset < 0:
            return _bad_request("offset must be >= 0")  # type: ignore[return-value]

        # Clean up the collection list (drop empties, dedupe while
        # preserving order so the response is stable for the client).
        seen: set[str] = set()
        clean_collections: list[str] = []
        for c in collections or []:
            c = (c or "").strip()
            if c and c not in seen:
                seen.add(c)
                clean_collections.append(c)

        try:
            # Return existing live session, or None if we need to
            # materialize a new one. The materialize runs in a thread
            # so the SQL `ORDER BY RANDOM()` doesn't block the event
            # loop. At 182 photos this is microseconds; at 2M it's
            # ~5s but paid once per session, then cached.
            existing = store.get(session)
            if existing is None:
                ids = await asyncio.to_thread(
                    index_db.shuffled_id_deck, tuple(clean_collections)
                )
                session_id, sess = store.put_new(ids)
            else:
                session_id, sess = existing
        except Exception:
            logger.exception("random session materialize failed")
            return JSONResponse(  # type: ignore[return-value]
                status_code=500,
                content=ErrorResponse(
                    error="internal_error",
                    detail="session materialize failed",
                    code="internal_error",
                ).model_dump(),
            )

        total = len(sess.ids)
        # Clamp offset to the deck length. An offset past the end
        # means the caller has walked the whole session; return
        # empty results with has_more=False.
        effective_offset = min(max(offset, 0), total)
        batch_ids = sess.ids[effective_offset : effective_offset + limit]

        # Materialize full SearchResults from the deck ids.
        rows: list[dict] = []
        if batch_ids:
            try:
                rows = index_db.rows_by_ids(batch_ids)
            except Exception:
                logger.exception("random deck lookup failed")
                return JSONResponse(  # type: ignore[return-value]
                    status_code=500,
                    content=ErrorResponse(
                        error="internal_error",
                        detail="deck lookup failed",
                        code="internal_error",
                    ).model_dump(),
                )
            # The rows come back in arbitrary order from the IN(...)
            # query. Re-order them to match the deck order — that's
            # the order the user is walking through.
            by_id = {str(r["id"]): r for r in rows}
            rows = [by_id[i] for i in batch_ids if i in by_id]

        results = _random_rows_to_results(rows, web_ui_url=cfg.web_ui_url)
        has_more = (effective_offset + len(results)) < total

        return SearchResponse(
            query="",
            positives=[],
            negatives=[],
            view=view,
            centroid=None,
            centroids=[],
            weights=None,
            results=results,
            took_ms=0,
            offset=effective_offset,
            limit=limit,
            has_more=has_more,
            session_id=session_id,
            session_total=total,
        )

    # Expose the session store for tests via the router object.
    # `router._random_session_store` is an implementation detail.
    router._random_session_store = store  # type: ignore[attr-defined]

    return router


def _bad_request(detail: str) -> JSONResponse:
    """Build a 400 JSONResponse with the documented error envelope."""
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error="bad_request", detail=detail, code="bad_request",
        ).model_dump(),
    )