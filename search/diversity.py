"""
search/diversity.py — Diversity service: persistence + IO orchestration.

Phase B3 (compute/IO separation): the pure ranking logic lives in
`search/diversity_compute.py`. This module owns:

- The DiversityResultCache class (LRU + TTL cache for rankings)
- The query-string parsing helpers (`resolve_diversity`, `resolve_pool_depth`)
- Re-exports of the public compute API for backward compat

The orchestration between this module and the compute module is
the `diversity_page` helper in `search/_indexed_helpers.py`. That's
the "service" layer that wires cache lookups → compute → cache
writes together with the Qdrant search.

---

## Native MMR branch (`test/native-mmr`)

The four-mode enum (off/low/balanced/high) and the per-mode strength
table are gone. `diversity` is a single float in `[0.0, 1.0]` passed
straight to `Mmr(diversity=...)`. `diversity_depth` is a free numeric
int (no fixed options; default 5000), clamped server-side by
`cfg.diversity_max_pool_depth`.

The relevance floor is a single constant in config
(`cfg.diversity_relevance_floor`), not a per-mode multiplier.

---

"""

from __future__ import annotations

import time
from dataclasses import dataclass

from search.diversity_compute import (
    DiversityRanking,
    DiversityStats,
    # Re-exported for tests that exercise the post-filter primitives
    # directly. These are private names in `diversity_compute` but the
    # diversity service exposes them as part of its public surface.
    _collapse_duplicate_indices,
    apply_relevance_floor,
)

__all__ = [  # noqa: RUF022
    # Value objects
    "DiversityRanking",
    "DiversityStats",
    # Pure compute (post-MMR filters)
    "apply_relevance_floor",
    "_collapse_duplicate_indices",
    # Parsing helpers (route-layer)
    "resolve_diversity",
    "resolve_pool_depth",
    # Persistence
    "DiversityResultCache",
]


# ---------------------------------------------------------------------------
# Parsing helpers (route-layer)
# ---------------------------------------------------------------------------


def resolve_diversity(diversity) -> float:
    """Resolve the diversity parameter from a query-string float.

    Accepts:
      - None / missing  -> 0.5 (balanced default)
      - numeric (int/float) -> clamped to [0.0, 1.0]
      - str numeric ("0.7") -> parsed

    Raises TypeError for invalid types, ValueError for non-numeric
    strings or out-of-range values.
    """
    if diversity is None:
        return 0.5
    if isinstance(diversity, (int, float)):
        value = float(diversity)
    elif isinstance(diversity, str):
        try:
            value = float(diversity)
        except ValueError as exc:
            raise ValueError(
                f"diversity must be a float in [0.0, 1.0]; got {diversity!r}"
            ) from exc
    else:
        raise TypeError(
            f"diversity must be a float in [0.0, 1.0]; got {diversity!r}"
        )
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            f"diversity must be a float in [0.0, 1.0]; got {value!r}"
        )
    return value


def resolve_pool_depth(depth, *, max_pool_depth: int) -> int:
    """Resolve the diversity pool depth from a query-string int.

    Accepts:
      - None / missing  -> max_pool_depth (default)
      - numeric (int/float/str) -> coerced, must be >= 1
      - values > max_pool_depth are clamped to max_pool_depth
        (the server-side ceiling) — we don't 400 because the user
        wanted "as much as possible", so we give them as much as we can.

    Raises TypeError for invalid types, ValueError for non-numeric
    strings or values < 1.
    """
    if depth is None:
        return max_pool_depth
    if isinstance(depth, bool):
        raise TypeError(
            f"diversity_depth must be an int >= 1; got {depth!r}"
        )
    if isinstance(depth, (int, float)):
        value = int(depth)
    elif isinstance(depth, str):
        try:
            value = int(depth)
        except ValueError as exc:
            raise ValueError(
                f"diversity_depth must be an int >= 1; got {depth!r}"
            ) from exc
    else:
        raise TypeError(
            f"diversity_depth must be an int >= 1; got {depth!r}"
        )
    if value < 1:
        raise ValueError(
            f"diversity_depth must be an int >= 1; got {value!r}"
        )
    if value > max_pool_depth:
        return max_pool_depth
    return value


# ---------------------------------------------------------------------------
# Persistence layer (the only IO-bearing code in this module)
# ---------------------------------------------------------------------------


@dataclass
class _CachedResult:
    hits: list
    stats: DiversityStats
    expires_at: float


class DiversityResultCache:
    """LRU + TTL cache for full Diversity rankings.

    A ranking is the entire pool_depth-sized ordered list. Subsequent
    requests with the same key slice into that list for `?offset` —
    no per-page rerank, regardless of depth.
    """

    def __init__(self, *, max_entries: int, ttl_seconds: float) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, _CachedResult] = {}
        self._lock = None

    def get(self, key: str) -> DiversityRanking | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.time():
            self._cache.pop(key, None)
            return None
        return DiversityRanking(hits=entry.hits, stats=entry.stats)

    def put(self, key: str, hits: list, stats: DiversityStats) -> None:
        if not hits and not stats.applied:
            return
        self._evict_if_full()
        self._cache[key] = _CachedResult(
            hits=list(hits),
            stats=stats,
            expires_at=time.time() + self._ttl_seconds,
        )

    def _evict_if_full(self) -> None:
        if len(self._cache) < self._max_entries:
            return
        oldest_key = next(iter(self._cache))
        self._cache.pop(oldest_key, None)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key) -> bool:  # type: ignore[reportArgumentType]
        return key in self._cache
