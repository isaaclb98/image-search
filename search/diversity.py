"""
search/diversity.py — Diversity service: persistence + IO orchestration.

Phase B3 (compute/IO separation): the pure ranking logic lives in
`search/diversity_compute.py`. This module owns:

- The DiversityResultCache class (LRU + TTL cache for rankings)
- The query-string parsing helpers (resolve_mode, resolve_depth,
  relevance_drop_for_mode) — they sit closer to the route layer
  than to pure compute, so they stay here.
- Re-exports of the public compute API for backward compat with
  existing callers (`from search.diversity import rank_diverse, ...`).

The orchestration between this module and the compute module is
the `diversity_page` helper in `search/_indexed_helpers.py`. That's
the "service" layer that wires cache lookups → compute → cache
writes together with the Qdrant search.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass

from search.diversity_compute import (  # noqa: F401
    DIVERSITY_AUTO_DEPTHS,
    DIVERSITY_DEPTH_OPTIONS,
    DIVERSITY_DEPTHS,
    DIVERSITY_MODE_RELEVANCE_MULTIPLIERS,
    DIVERSITY_MODE_STRENGTHS,
    DIVERSITY_MODES,
    DiversityRanking,
    DiversityStats,
    # Re-exported for tests/test_diversity.py and any external consumer
    # that exercises the ranking helpers directly. These are private
    # names in `diversity_compute` but the diversity service exposes
    # them as part of its public surface; see §B3 step 42.
    _collapse_duplicate_indices,
    _cosine_sim,
    _normalise_matrix,
    _normalise_vector,
)

# Re-export the pure compute surface so existing call sites continue
# to import from `search.diversity` without a sweeping import rewrite.
__all__ = [
    # Constants
    "DIVERSITY_AUTO_DEPTHS",
    "DIVERSITY_DEPTHS",
    "DIVERSITY_DEPTH_OPTIONS",
    "DIVERSITY_MODE_RELEVANCE_MULTIPLIERS",
    "DIVERSITY_MODE_STRENGTHS",
    "DIVERSITY_MODES",
    # Value objects
    "DiversityRanking",
    "DiversityStats",
    # Pure compute
    "mmr_rerank",
    "rank_diverse",
    # Parsing helpers (route-layer)
    "relevance_drop_for_mode",
    "resolve_depth",
    "resolve_mode",
    # Persistence
    "DiversityResultCache",
]


# ---------------------------------------------------------------------------
# Parsing helpers (route-layer; live here because they share the constants)
# ---------------------------------------------------------------------------


def resolve_mode(mode: str | None, legacy_diverse: bool = False) -> tuple[str, float]:
    """Resolve the diversity mode + strength from query params.

    Accepts the legacy boolean (`diverse=True`) as well as the
    explicit mode string. Returns the canonical mode name and
    the corresponding strength knob. "off" is always valid.
    """
    if mode is None:
        if legacy_diverse:
            return "balanced", DIVERSITY_MODE_STRENGTHS["balanced"]
        return "off", 0.0
    if mode == "off":
        return "off", 0.0
    if mode not in DIVERSITY_MODE_STRENGTHS:
        raise ValueError(
            f"diversity must be one of {sorted(DIVERSITY_MODE_STRENGTHS)}; "
            f"got {mode!r}"
        )
    return mode, DIVERSITY_MODE_STRENGTHS[mode]


def resolve_depth(depth: str | None, mode: str = "off") -> tuple[str, int]:
    """Resolve the diversity pool depth from query params.

    Returns the canonical depth label and the integer pool size.
    `"auto"` picks a mode-appropriate default.
    """
    if depth is None or depth == "auto":
        if mode == "off":
            return "auto", 0
        return "auto", DIVERSITY_AUTO_DEPTHS[mode]
    if depth not in DIVERSITY_DEPTHS:
        raise ValueError(
            f"diversity_depth must be one of {('auto', *DIVERSITY_DEPTHS)}; "
            f"got {depth!r}"
        )
    return depth, DIVERSITY_DEPTHS[depth]


def relevance_drop_for_mode(mode: str, base_drop: float) -> float:
    """Apply the per-mode relevance-drop multiplier.

    High mode tolerates more relevance loss for diversity; low mode
    tolerates less. The base_drop is the caller-configured default.
    """
    return base_drop * DIVERSITY_MODE_RELEVANCE_MULTIPLIERS.get(mode, 1.0)


# ---------------------------------------------------------------------------
# Pure compute (re-exported above; the actual implementations live in
# search/diversity_compute.py — kept here so existing callers don't
# need to update their imports.)
# ---------------------------------------------------------------------------

# Local re-imports of mmr_rerank + rank_diverse so they're part of this
# module's namespace (callers do `from search.diversity import rank_diverse`).
# We can't re-export them with `from ... import` above without triggering
# circular imports because diversity_compute imports this module's
# dataclasses. So we wrap them in thin aliases below.
import search.diversity_compute as _compute

mmr_rerank = _compute.mmr_rerank
rank_diverse = _compute.rank_diverse


# ---------------------------------------------------------------------------
# Persistence layer (the only IO-bearing code in this module)
# ---------------------------------------------------------------------------


@dataclass
class _CachedResult:
    """One cached Diversity ranking: tuple of hits + stats + timestamp."""

    created_at: float
    hits: tuple
    stats: DiversityStats


class DiversityResultCache:
    """Small TTL cache for one user's ordered Diversity result sets.

    Bounded LRU: when full, the oldest entry is evicted. The TTL
    bounds staleness — a request that arrives after `ttl_seconds`
    since `put()` is treated as a miss and recomputed.

    This is the only IO-bearing piece of the Diversity feature. It
    lives in this module (not `diversity_compute.py`) precisely
    because it holds mutable state.
    """

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 64):
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._entries: dict[str, _CachedResult] = {}

    def get(self, key: str) -> _CachedResult | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self.ttl_seconds == 0 or time.monotonic() - entry.created_at >= self.ttl_seconds:
            self._entries.pop(key, None)
            return None
        return entry

    def put(self, key: str, hits: Iterable, stats: DiversityStats) -> None:
        if self.ttl_seconds == 0:
            return
        if key in self._entries:
            self._entries.pop(key, None)
        self._entries[key] = _CachedResult(
            created_at=time.monotonic(),
            hits=tuple(hits),
            stats=stats,
        )
        while len(self._entries) > self.max_entries:
            self._entries.pop(next(iter(self._entries)))

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
