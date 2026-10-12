"""
search/surprise_deck.py — Surprise Me deck generator + per-query cache.

The Surprise Me feature pre-generates a fixed-size list of photos drawn
from a Gumbel-perturbed softmax over the deep Qdrant ranking. The user
scrolls through their private randomised deck; subsequent pages
reference the cached deck by session id (no Qdrant round-trip).

This module owns:
  - `gumbel_top_k` — pure numpy helper. Takes a score array and a
    sampling temperature, returns a length-k array of indices into
    the original array, sampled without replacement from the
    softmax distribution `p_i ∝ exp(score_i / temperature)`.
  - `SurpriseDeckCache` — process-local map of cache_key → deck.
    Mirrors `_RandomSessionStore` in `search/routers/random.py`
    (session TTL, in-memory dict, explicit `clear()` for tests).
  - The "cache_key" itself: a sha256 hash over every search param
    that influences the deck (q, positives, negatives, centroids,
    weights, collections, favorites, filename, temperature,
    pool_size, seed). Used as the session id so the ETag and the
    cache lookup are one mechanism.

Gumbel-max sampling: for each candidate draw an independent Gumbel
sample `g = -log(-log(U))` where U is uniform in (0, 1). The
argmax of `score + T * g` is a sample from the softmax
distribution. Iterating with the chosen index masked to -inf gives
k distinct samples without replacement — O(k * n) numpy argmax
calls, ~tens of ms for n=10000, k=2400.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np


def gumbel_top_k(
    scores: Sequence[float],
    k: int,
    temperature: float,
    rng: np.random.Generator,
) -> list[int]:
    """
    Sample k distinct indices from `scores` under a softmax distribution.

    The probability of index i is `exp(scores[i] / T) / Z` where
    Z is the normalising constant. Indices are returned in the
    order they were drawn (not sorted by score). The sample is
    without replacement.

    Args:
        scores: 1-D score array. Does not need to be normalised.
        k: Number of distinct indices to return. Must satisfy
           0 <= k <= len(scores).
        temperature: Gumbel noise scale. T=0 is deterministic
            (always the top-k); T→∞ is uniform random.
        rng: numpy Generator used to draw uniform noise. Pass a
            seeded generator for reproducible decks.

    Returns:
        List of `k` distinct indices into `scores`, in draw order.
    """
    n = len(scores)
    if k < 0 or k > n:
        raise ValueError(f"k must be in [0, {n}]; got {k}")
    if k == 0:
        return []
    if temperature <= 0:
        # Deterministic: return the top-k by score in draw order.
        order = np.argsort(-np.asarray(scores, dtype=np.float64), kind="stable")[:k]
        return [int(i) for i in order]
    arr = np.asarray(scores, dtype=np.float64).copy()
    picked: list[int] = []
    gumbel = -np.log(-np.log(rng.uniform(low=1e-12, high=1 - 1e-12, size=n)))
    for _ in range(k):
        perturbed = arr + temperature * gumbel
        idx = int(np.argmax(perturbed))
        picked.append(idx)
        arr[idx] = -np.inf
    return picked


def compute_cache_key(
    *,
    q: str,
    positives: list[str],
    negatives: list[str],
    centroids: list[str],
    weights: list[float] | None,
    collections: list[str],
    favorites: bool,
    filename: str,
    temperature: float,
    pool_size: int,
    seed: int,
) -> str:
    """
    Deterministic 16-char cache key for a Surprise Me deck.

    The key is a sha256 over every search param that influences
    which photos end up in the deck. Two requests with the same
    key get the same deck; the key doubles as the session id
    passed back by the client (so the ETag and the cache lookup
    are the same value — no separate session store needed).

    Fields are joined with `\\x1f` (ASCII unit separator) so a
    value containing `|` or `,` cannot collide with a different
    field's value. See `routers/search.py` ETag block for the
    matching pattern.
    """
    weights_str = ",".join(f"{w:.6f}" for w in (weights or []))
    etag_input = "\x1f".join(
        [
            q,
            ",".join(positives),
            ",".join(negatives),
            ",".join(centroids),
            weights_str,
            ",".join(collections),
            str(favorites),
            filename,
            f"{temperature:.6f}",
            str(pool_size),
            str(seed),
        ]
    ).encode("utf-8")
    return hashlib.sha256(etag_input).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Process-local deck cache.
# ---------------------------------------------------------------------------


DEFAULT_DECK_SIZE = 2400
DEFAULT_TEMPERATURE = 0.05
DEFAULT_POOL_SIZE = 5000
TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 1.0
POOL_SIZE_MIN = 200
POOL_SIZE_MAX = 10_000

DECK_TTL_S = 30 * 60


@dataclass
class _DeckEntry:
    """One cached Surprise Me deck: list of point ids + timestamp."""

    created_at: float
    point_ids: tuple[str, ...]


class SurpriseDeckCache:
    """
    Process-local map of cache_key → pre-generated deck.

    Deliberately NOT shared across processes — a deck is cheap
    to rebuild (one Qdrant search + numpy Gumbel draw), and
    cross-process state would mean sticky sessions on a load
    balancer, which is the opposite of what Surprise Me wants.

    Mirrors `_RandomSessionStore` in `search/routers/random.py`:
    same TTL semantics, same in-memory dict, same explicit
    `clear()` for tests. The cache_key (not a token) is used as
    the session id, so the same query+params+seed always hits
    the same cache entry.
    """

    def __init__(self, ttl_s: float = DECK_TTL_S):
        self._decks: dict[str, _DeckEntry] = {}
        self._ttl_s = ttl_s

    def get(self, cache_key: str) -> tuple[str, _DeckEntry] | None:
        """Look up a live deck by cache key. Returns None if the
        key is missing or the deck has expired (entry is removed
        on expiry)."""
        if not cache_key:
            return None
        entry = self._decks.get(cache_key)
        if entry is None:
            return None
        if (time.monotonic() - entry.created_at) >= self._ttl_s:
            del self._decks[cache_key]
            return None
        return cache_key, entry

    def put(self, cache_key: str, point_ids: Iterable[str]) -> str:
        """Materialise a fresh deck under the given key. Returns
        the same key (the session id is the cache key)."""
        self._decks[cache_key] = _DeckEntry(
            created_at=time.monotonic(),
            point_ids=tuple(point_ids),
        )
        return cache_key

    def clear(self) -> None:
        """Drop all decks. Used by tests."""
        self._decks.clear()

    def __len__(self) -> int:
        return len(self._decks)
