"""Search-only Diversity ranking.

This module deliberately does not power Discovery.  Search Diversity has a
different contract: preserve query relevance, suppress duplicate images, and
produce one deterministic ordering that the API can paginate.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)

DIVERSITY_MODE_STRENGTHS: dict[str, float] = {
    "low": 0.25,
    "balanced": 0.50,
    "high": 0.78,
}
DIVERSITY_MODES = ("off", *DIVERSITY_MODE_STRENGTHS)


@dataclass(frozen=True)
class DiversityStats:
    """Diagnostics describing one Diversity ranking run."""

    requested: bool = False
    applied: bool = False
    mode: str = "off"
    strength: float = 0.0
    candidate_count: int = 0
    result_count: int = 0
    duplicate_images_collapsed: int = 0
    semantic_groups_covered: int = 0


@dataclass(frozen=True)
class DiversityRanking:
    """Ordered hits plus diagnostics for a complete candidate pool."""

    hits: list
    stats: DiversityStats


@dataclass
class _CachedResult:
    created_at: float
    hits: tuple
    stats: DiversityStats


class DiversityResultCache:
    """Small TTL cache for one user's ordered Diversity result sets."""

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


def resolve_mode(mode: str | None, legacy_diverse: bool = False) -> tuple[str, float]:
    """Resolve the public Diversity URL contract.

    ``diverse=true`` remains a backwards-compatible alias for Balanced.
    An explicit ``diversity=off|low|balanced|high`` value wins over the
    legacy boolean so URLs can be migrated without ambiguity.
    """
    if mode is None or not mode.strip():
        return ("balanced", DIVERSITY_MODE_STRENGTHS["balanced"]) if legacy_diverse else ("off", 0.0)
    normalized = mode.strip().lower()
    if normalized == "off":
        return "off", 0.0
    if normalized not in DIVERSITY_MODE_STRENGTHS:
        allowed = ", ".join(DIVERSITY_MODES)
        raise ValueError(f"diversity must be one of: {allowed}")
    return normalized, DIVERSITY_MODE_STRENGTHS[normalized]


def mmr_rerank(
    hits_with_vectors: list[tuple],
    query_vector: list[float],
    k: int,
    lambda_: float = 0.5,
) -> list:
    """Backwards-compatible MMR helper retained for existing callers/tests.

    ``lambda_`` keeps the historical meaning here: 1.0 is pure relevance,
    0.0 is maximum diversity. The search endpoint uses ``rank_diverse`` below,
    whose public strength has the clearer meaning 0.0 = off and 1.0 = high.
    """
    if not hits_with_vectors or k <= 0:
        return [h for h, _v in hits_with_vectors[:k]]
    if not 0.0 <= lambda_ <= 1.0:
        raise ValueError("lambda_ must be in [0, 1]")

    try:
        import numpy as np

        vectors = np.asarray([_as_float_list(v) for _h, v in hits_with_vectors], dtype=np.float32)
        query = np.asarray(_as_float_list(query_vector), dtype=np.float32)
        if vectors.ndim != 2 or query.ndim != 1 or vectors.shape[1] != query.shape[0]:
            raise ValueError("query and candidate vector dimensions must match")
        if not np.isfinite(vectors).all() or not np.isfinite(query).all():
            raise ValueError("query and candidate vectors must be finite")
        vectors = _normalise_matrix(vectors)
        query = _normalise_vector(query)
        query_scores = vectors @ query
        pairwise = vectors @ vectors.T
        selected: list[int] = []
        target = min(k, len(hits_with_vectors))
        while len(selected) < target:
            if not selected:
                remaining = np.arange(len(hits_with_vectors))
                best = int(remaining[np.argmax(query_scores[remaining])])
            else:
                remaining = np.asarray(
                    [i for i in range(len(hits_with_vectors)) if i not in selected],
                    dtype=np.int32,
                )
                redundancy = pairwise[np.ix_(remaining, np.asarray(selected))].max(axis=1)
                values = lambda_ * query_scores[remaining] - (1.0 - lambda_) * redundancy
                best = int(remaining[np.argmax(values)])
            selected.append(best)
        return [hits_with_vectors[i][0] for i in selected]
    except ImportError:
        # Keep the pure-Python fallback for unusual deployments and make the
        # failure explicit rather than returning a partially ranked result.
        selected: list[tuple] = []
        candidates = list(hits_with_vectors)
        while len(selected) < min(k, len(hits_with_vectors)) and candidates:
            best_idx = 0
            best_score = -float("inf")
            for i, (hit, vec) in enumerate(candidates):
                q_sim = _cosine_sim(query_vector, vec)
                max_sim = max(
                    (_cosine_sim(vec, selected_vec) for _hit, selected_vec in selected),
                    default=0.0,
                )
                score = lambda_ * q_sim - (1.0 - lambda_) * max_sim
                if score > best_score:
                    best_score = score
                    best_idx = i
            selected.append(candidates.pop(best_idx))
        return [h for h, _v in selected]


def rank_diverse(
    hits_with_vectors: list[tuple],
    query_vector: list[float],
    *,
    mode: str = "balanced",
    strength: float | None = None,
    max_results: int | None = None,
    duplicate_hamming_distance: int = 10,
    relevance_drop: float = 0.10,
    semantic_novelty_threshold: float = 0.88,
) -> DiversityRanking:
    """Rank a complete search candidate pool for the Diversity feature.

    Duplicate groups are collapsed before greedy MMR. Relevance is normalized
    within this candidate pool, but candidates outside a raw cosine relevance
    floor are not allowed to win merely because they are different.
    """
    if mode not in DIVERSITY_MODES:
        raise ValueError(f"unknown diversity mode: {mode!r}")
    if mode == "off":
        return DiversityRanking(
            hits=[h for h, _v in hits_with_vectors[:max_results]],
            stats=DiversityStats(
                requested=False,
                applied=False,
                mode="off",
                candidate_count=len(hits_with_vectors),
                result_count=(
                    len(hits_with_vectors)
                    if max_results is None
                    else min(len(hits_with_vectors), max_results)
                ),
            ),
        )
    if strength is None:
        strength = DIVERSITY_MODE_STRENGTHS[mode]
    if not math.isfinite(strength) or not 0.0 <= strength <= 1.0:
        raise ValueError("diversity strength must be finite and in [0, 1]")
    if duplicate_hamming_distance < 0:
        raise ValueError("duplicate_hamming_distance must be >= 0")
    if relevance_drop < 0 or not math.isfinite(relevance_drop):
        raise ValueError("relevance_drop must be finite and >= 0")
    if not hits_with_vectors or max_results == 0:
        return DiversityRanking(
            hits=[],
            stats=DiversityStats(
                requested=True, applied=True, mode=mode, strength=strength,
                candidate_count=len(hits_with_vectors),
            ),
        )

    import numpy as np

    hits = [h for h, _v in hits_with_vectors]
    vectors = np.asarray([_as_float_list(v) for _h, v in hits_with_vectors], dtype=np.float32)
    query = np.asarray(_as_float_list(query_vector), dtype=np.float32)
    if vectors.ndim != 2 or query.ndim != 1 or vectors.shape[1] != query.shape[0]:
        raise ValueError("query and candidate vector dimensions must match")
    if not np.isfinite(vectors).all() or not np.isfinite(query).all():
        raise ValueError("query and candidate vectors must be finite")
    vectors = _normalise_matrix(vectors)
    query = _normalise_vector(query)
    query_scores = vectors @ query

    keep_indices = _collapse_duplicate_indices(
        hits,
        query_scores=query_scores,
        duplicate_hamming_distance=duplicate_hamming_distance,
    )
    duplicate_count = len(hits) - len(keep_indices)
    vectors = vectors[keep_indices]
    query_scores = query_scores[keep_indices]
    kept_hits = [hits[i] for i in keep_indices]
    if not len(kept_hits):
        return DiversityRanking(
            hits=[],
            stats=DiversityStats(
                requested=True, applied=True, mode=mode, strength=strength,
                candidate_count=len(hits_with_vectors),
                duplicate_images_collapsed=duplicate_count,
            ),
        )

    target = len(kept_hits) if max_results is None else min(max_results, len(kept_hits))
    if strength <= 0.0 or target <= 1:
        order = np.argsort(-query_scores, kind="stable")[:target]
        ordered = [kept_hits[int(i)] for i in order]
        return DiversityRanking(
            hits=ordered,
            stats=DiversityStats(
                requested=True, applied=True, mode=mode, strength=strength,
                candidate_count=len(hits_with_vectors), result_count=len(ordered),
                duplicate_images_collapsed=duplicate_count,
                semantic_groups_covered=len(ordered),
            ),
        )

    pairwise = vectors @ vectors.T
    top_score = float(np.max(query_scores))
    eligible = query_scores >= top_score - relevance_drop
    if not bool(eligible.any()):
        eligible[:] = True

    score_min = float(np.min(query_scores))
    score_span = float(np.max(query_scores) - score_min)
    relevance = (
        np.ones(len(query_scores), dtype=np.float32)
        if score_span <= 1e-8
        else (query_scores - score_min) / score_span
    )

    selected: list[int] = []
    semantic_groups = 0
    while len(selected) < target:
        if not selected:
            candidates = np.flatnonzero(eligible)
            best = int(candidates[np.argmax(query_scores[candidates])])
            semantic_groups = 1
        else:
            remaining = np.asarray(
                [i for i in range(len(kept_hits)) if i not in selected],
                dtype=np.int32,
            )
            if not len(remaining):
                break
            redundancy = pairwise[np.ix_(remaining, np.asarray(selected))].max(axis=1)
            values = (1.0 - strength) * relevance[remaining] + strength * (1.0 - redundancy)
            eligible_remaining = eligible[remaining]
            if bool(eligible_remaining.any()):
                values = np.where(eligible_remaining, values, -np.inf)
            best_position = int(np.argmax(values))
            best = int(remaining[best_position])
            if float(redundancy[best_position]) < semantic_novelty_threshold:
                semantic_groups += 1
        selected.append(best)

    ordered = [kept_hits[i] for i in selected]
    return DiversityRanking(
        hits=ordered,
        stats=DiversityStats(
            requested=True,
            applied=True,
            mode=mode,
            strength=strength,
            candidate_count=len(hits_with_vectors),
            result_count=len(ordered),
            duplicate_images_collapsed=duplicate_count,
            semantic_groups_covered=semantic_groups,
        ),
    )


def _collapse_duplicate_indices(
    hits: list,
    *,
    query_scores,
    duplicate_hamming_distance: int,
) -> list[int]:
    """Keep the highest-relevance representative of fingerprint groups."""
    from indexer.fingerprints import hamming_distance

    parent = list(range(len(hits)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    exact: dict[str, int] = {}
    dhashes: list[tuple[str, int]] = []
    for index, hit in enumerate(hits):
        payload = getattr(hit, "payload", None) or {}
        content_hash = payload.get("content_sha256")
        if content_hash:
            previous = exact.get(str(content_hash))
            if previous is not None:
                union(index, previous)
            else:
                exact[str(content_hash)] = index
        image_hash = payload.get("dhash")
        if image_hash:
            image_hash = str(image_hash)
            for previous_hash, previous_index in dhashes:
                distance = hamming_distance(image_hash, previous_hash)
                if distance is not None and distance <= duplicate_hamming_distance:
                    union(index, previous_index)
                    break
            dhashes.append((image_hash, index))

    representatives: dict[int, int] = {}
    for index in range(len(hits)):
        root = find(index)
        current = representatives.get(root)
        if current is None or float(query_scores[index]) > float(query_scores[current]):
            representatives[root] = index
    return sorted(representatives.values())


def _normalise_vector(vector):
    import numpy as np

    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        return np.zeros_like(vector)
    return vector / norm


def _normalise_matrix(matrix):
    import numpy as np

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Dot product between two unit-norm vectors."""
    return sum(ai * bi for ai, bi in zip(a, b))


def _as_float_list(v) -> list[float]:
    """Normalize a Qdrant/numpy vector to a flat list of floats."""
    if hasattr(v, "tolist"):
        v = v.tolist()
    return [float(item) for item in v]
