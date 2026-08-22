"""
search/routers/discover.py — /api/discover/* (§B2 step 8).

Discovery endpoints:

- POST /api/discover/start: create a new discovery session and
  return the first pair of candidate images.
- POST /api/discover/pick:  record the user's pick for the
  current pair, advance the session, return the next pair
  (or `null` if the session ended).

The compute side lives in `search.discover` (start_session,
submit_pick, DiscoverOptions, etc.). The router is a thin
shell that wraps Qdrant errors as 502 and hydrates each
returned pair's image URLs with the live `web_ui_url`.

`pair=None` (session gone, expired, fake id) is the documented
"end of session" signal — the frontend treats it as "redirect
to /discover/start" rather than an error.

Tests pin:
- The two endpoints return the documented JSON shape.
- Qdrant ConnectionError / OSError surfaces as 502.
- `pair=None` is preserved through hydration (no exception).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from search import discover
from search.image_resolver import resolve_url
from search.models import DiscoveryPair, DiscoveryPickResponse, DiscoveryStartResponse

logger = logging.getLogger(__name__)


def _hydrate_pair_urls(pair: DiscoveryPair | None, web_ui_url: str) -> DiscoveryPair | None:
    """Fill in the public /photo/{id}/raw URL on each image.

    discover.py builds pairs with empty URLs because it doesn't
    know the web_ui_url. We patch them in here, where the
    config is available.
    """
    if pair is None:
        return None
    if pair.left is not None and not pair.left.url:
        pair.left.url = resolve_url(pair.left.id, web_ui_url)
    if pair.right is not None and not pair.right.url:
        pair.right.url = resolve_url(pair.right.id, web_ui_url)
    return pair


def build_discover_router(
    *,
    qdrant: Any,
    cfg: Any,
    index_db: Any,
) -> APIRouter:
    """Build the discover router with the live dependencies."""
    router = APIRouter()

    @router.post("/api/discover/start", response_model=DiscoveryStartResponse)
    async def discover_start() -> DiscoveryStartResponse:
        """Create a new discovery session and return the first pair."""
        try:
            session_id, pair = discover.start_session(
                qdrant, discover.DiscoverOptions.from_config(cfg), index_db,
            )
        except (ConnectionError, OSError) as e:
            logger.warning("Qdrant unreachable for /api/discover/start: %s", e)
            raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
        return DiscoveryStartResponse(
            session_id=session_id,
            pair=_hydrate_pair_urls(pair, cfg.web_ui_url),  # type: ignore[arg-type]
        )

    @router.post("/api/discover/pick", response_model=DiscoveryPickResponse)
    async def discover_pick(
        session_id: str = Query(..., description="discovery session id"),
        image_id: str = Query(..., description="the image id the user picked"),
    ) -> DiscoveryPickResponse:
        """Record a pick and return the next pair (or null if ended).

        Returns pair=None if the session is gone (expired TTL,
        server restart, fake id). The frontend treats that as
        "session ended, start over" and redirects to /discover.

        The response also carries `round` (1-indexed round number
        the user is on) and `liked_count` (cumulative likes since
        session start) so the UI can show progress without a second
        round-trip.
        """
        try:
            next_pair = discover.submit_pick(
                qdrant, session_id, image_id,
                discover.DiscoverOptions.from_config(cfg), index_db,
            )
        except (ConnectionError, OSError) as e:
            logger.warning("Qdrant unreachable for /api/discover/pick: %s", e)
            raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
        session = discover.get_session(session_id)
        liked_count = len(session.liked) if session else 0
        round_completed = session.round if session else 0
        return DiscoveryPickResponse(
            pair=_hydrate_pair_urls(next_pair, cfg.web_ui_url),  # type: ignore[arg-type]
            round=round_completed,
            liked_count=liked_count,
        )

    return router
