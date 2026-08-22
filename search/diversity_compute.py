"""
search/diversity_compute.py — Pure compute for the Diversity ranking feature.

Phase B3 (compute/IO separation): every function in this module is
deterministic and side-effect-free. The only inputs are the candidate
hits + query vector + knobs; the only outputs are ordered hits +
diagnostics. No Qdrant, no filesystem, no network, no logging beyond
debug-level invariants.

This makes the module trivially unit-testable:
- feed it a hand-crafted list of hits
- assert the returned ranking
- assert DiversityStats reflects the run

For IO concerns (cache lookup, persistence, orchestration with the
Qdrant search-side), see `search/diversity.py` (persistence) and
`search/_indexed_helpers.py::diversity_page` (service).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIVERSITY_MODE_STRENGTHS: dict[str, float] = {
    "low": 0.25,
    "balanced": 0.50,
    "high": 0.88,
}
DIVERSITY_MODES = ("off", *DIVERSITY_MODE_STRENGTHS)

DIVERSITY_DEPTHS: dict[str, int] = {
    "500": 500,
    "1000": 1000,
    "2000": 2000,
    "5000": 5000,
}
DIVERSITY_DEPTH_OPTIONS = ("auto", *DIVERSITY_DEPTHS)

DIVERSITY_AUTO_DEPTHS: dict[str, int] = {
    "low": 500,
    "balanced": 1000,
    "high": 2000,
}

DIVERSITY_MODE_RELEVANCE_MULTIPLIERS: dict[str, float] = {
    "low": 0.60,
    "balanced": 1.00,
    "high": 1.80,
}


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


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
    depth: str = "auto"
    pool_depth: int = 0


@dataclass(frozen=True)
class DiversityRanking:
    """Ordered hits plus diagnostics for a complete candidate pool."""

    hits: list
    stats: DiversityStats


# ---------------------------------------------------------------------------
# Pure helpers (moved verbatim from search/diversity.py)
# ---------------------------------------------------------------------------


def _normalise_vector(vector):
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        return np.zeros_like(vector)
    return vector / norm


def _normalise_matrix(matrix):
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


def _cosine_sim(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _as_float_list(v) -> list[float]:
    if v is None:
        return []
    if isinstance(v, list):
        return [float(x) for x in v]
    return []


def _collapse_duplicate_indices(
    hits: list,
    *,
    query_scores,
    duplicate_hamming_distance: int,
) -> list[int]:
    """Keep the highest-relevance representative of fingerprint groups."""
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
    # Actual indexed dHash values are 64-bit (16 hexadecimal digits). Keep
    # values grouped by width because hamming_distance intentionally rejects
    # hashes with different widths.
    dhash_groups: dict[int, list[tuple[int, int]]] = {}
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
            image_hash = str(image_hash).strip().lower()
            if (
                len(image_hash) <= 16
                and image_hash
                and all(char in "0123456789abcdef" for char in image_hash)
            ):
                try:
                    value = int(image_hash, 16)
                except ValueError:
                    value = None
                if value is not None:
                    dhash_groups.setdefault(len(image_hash) * 4, []).append((value, index))
    # Collapse near-duplicate dHash groups when within distance.
    for width, group in dhash_groups.items():
        if duplicate_hamming_distance <= 0 or len(group) < 2:
            continue
        ordered = sorted(group, key=lambda pair: pair[1])
        for left_pos in range(len(ordered)):
            left_value, left_index = ordered[left_pos]
            for right_pos in range(left_pos + 1, len(ordered)):
                right_value, right_index = ordered[right_pos]
                if right_index - left_index > 256:
                    break
                xor = left_value ^ right_value
                if xor.bit_count() <= duplicate_hamming_distance:
                    union(left_index, right_index)
    # Pick the highest-relevance representative per group.
    groups: dict[int, list[int]] = {}
    for index in range(len(hits)):
        groups.setdefault(find(index), []).append(index)
    representatives: list[int] = []
    for members in groups.values():
        best = max(members, key=lambda i: query_scores[i])
        representatives.append(best)
    representatives.sort()
    return representatives
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
        selected_mask = np.zeros(len(hits_with_vectors), dtype=bool)
        max_redundancy = np.full(
            len(hits_with_vectors), -np.inf, dtype=np.float32,
        )
        target = min(k, len(hits_with_vectors))
        while len(selected) < target:
            if not selected:
                remaining = np.arange(len(hits_with_vectors))
                best = int(remaining[np.argmax(query_scores[remaining])])
            else:
                remaining = np.flatnonzero(~selected_mask)
                values = (
                    lambda_ * query_scores[remaining]
                    - (1.0 - lambda_) * max_redundancy[remaining]
                )
                best = int(remaining[np.argmax(values)])
            selected.append(best)
            selected_mask[best] = True
            max_redundancy = np.maximum(max_redundancy, pairwise[:, best])
        return [hits_with_vectors[i][0] for i in selected]
    except ImportError:
        # Keep the pure-Python fallback for unusual deployments and make the
        # failure explicit rather than returning a partially ranked result.
        selected: list[tuple] = []
        candidates = list(hits_with_vectors)
        while len(selected) < min(k, len(hits_with_vectors)) and candidates:
            best_idx = 0
            best_score = -float("inf")
            for i, (_hit, vec) in enumerate(candidates):
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
    depth: str = "auto",
    pool_depth: int | None = None,
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
    if not 0 <= duplicate_hamming_distance <= 64:
        raise ValueError("duplicate_hamming_distance must be between 0 and 64")
    if relevance_drop < 0 or not math.isfinite(relevance_drop):
        raise ValueError("relevance_drop must be finite and >= 0")
    if depth not in DIVERSITY_DEPTH_OPTIONS:
        raise ValueError(f"unknown diversity depth: {depth!r}")
    if pool_depth is not None and pool_depth < 0:
        raise ValueError("pool_depth must be >= 0")
    actual_pool_depth = len(hits_with_vectors) if pool_depth is None else int(pool_depth)
    if not hits_with_vectors or max_results == 0:
        return DiversityRanking(
            hits=[],
            stats=DiversityStats(
                requested=True, applied=True, mode=mode, strength=strength,
                candidate_count=len(hits_with_vectors),
                depth=depth,
                pool_depth=actual_pool_depth,
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
                depth=depth,
                pool_depth=actual_pool_depth,
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
                depth=depth,
                pool_depth=actual_pool_depth,
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
    selected_mask = np.zeros(len(kept_hits), dtype=bool)
    max_redundancy = np.full(len(kept_hits), -np.inf, dtype=np.float32)
    semantic_groups = 0
    while len(selected) < target:
        if not selected:
            candidates = np.flatnonzero(eligible)
            best = int(candidates[np.argmax(query_scores[candidates])])
            semantic_groups = 1
        else:
            remaining = np.flatnonzero(~selected_mask)
            if not len(remaining):
                break
            redundancy = max_redundancy[remaining]
            values = (
                (1.0 - strength) * relevance[remaining]
                + strength * (1.0 - redundancy)
            )
            eligible_remaining = eligible[remaining]
            if bool(eligible_remaining.any()):
                values = np.where(eligible_remaining, values, -np.inf)
            best_position = int(np.argmax(values))
            best = int(remaining[best_position])
            if float(redundancy[best_position]) < semantic_novelty_threshold:
                semantic_groups += 1
        selected.append(best)
        selected_mask[best] = True
        max_redundancy = np.maximum(max_redundancy, pairwise[:, best])

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
            depth=depth,
            pool_depth=actual_pool_depth,
        ),
    )


