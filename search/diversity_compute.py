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

---

## Native MMR branch (`test/native-mmr`)

This module on this branch is a **clean replacement**, not a co-existing
fallback. The MMR itself runs server-side in Qdrant
(`query_points(query=NearestQuery(nearest=vec, mmr=Mmr(...)))`); the
only Python-side work that remains is the two load-bearing post-filters
that native MMR doesn't do:

  - **dhash collapse** (`_collapse_duplicate_indices`): keep the
    highest-relevance representative of fingerprint groups so the
    result page doesn't show 5 photos from the same burst.
  - **relevance floor** (`apply_relevance_floor`): only candidates
    within a relevance band can win; without it, MMR could surface a
    weak-but-diverse candidate over a strong-relevant-and-similar one.

The pure MMR selection loop (`rank_diverse`, `mmr_rerank`, the
`vectors @ vectors.T` greedy walk, `_as_float_list`) is not on this
branch — it's replaced by Qdrant's server-side rerank.

The four-mode enum (off/low/balanced/high) and the per-mode strength
table are gone. `diversity` is a single float in `[0.0, 1.0]` passed
straight to `Mmr(diversity=...)`. The relevance drop is a single
constant in config (default 0.10), not a per-mode multiplier.

---

"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiversityStats:
    """Diagnostics describing one Diversity ranking run."""

    requested: bool = False
    applied: bool = False
    diversity: float = 0.0
    candidate_count: int = 0
    result_count: int = 0
    pool_depth: int = 0
    mmr_source: str = "qdrant_native"


@dataclass(frozen=True)
class DiversityRanking:
    """Ordered hits plus diagnostics for a complete candidate pool."""

    hits: list
    stats: DiversityStats


# ---------------------------------------------------------------------------
# Dhash collapse (post-MMR filter)
# ---------------------------------------------------------------------------


def _collapse_duplicate_indices(
    hits: list,
    *,
    query_scores,
    duplicate_hamming_distance: int,
) -> list[int]:
    """Keep the highest-relevance representative of fingerprint groups.

    The inner dHash hamming comparison is the hot path at depth 5000
    (~2.4M `int.bit_count()` calls per call in the original Python
    double-loop). We pre-parse dHash strings into a uint64 numpy array
    and a parallel width bucket, then for each width bucket compute a
    banded XOR matrix (upper triangle, width W=256 matching the
    original `right - left > 256: break` window) and popcount via
    byte-level bit-unpacking — fully vectorized, no per-element Python
    work.

    Behavior is byte-identical to the prior Python loop:
      * content_sha256 exact-match unions (Python dict, unchanged)
      * dHash near-duplicate unions with the same width-bucket
        partition and 256-element neighborhood window
      * representative pick = highest query_score per union-find
        group, sorted by original index
    """
    parent = list(range(len(hits)))

    def find(idx: int) -> int:
        while parent[idx] != idx:
            parent[idx] = parent[parent[idx]]
            idx = parent[idx]
        return idx

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra > rb:
                ra, rb = rb, ra
            parent[rb] = ra

    # ----- pass 1: exact content_sha256 unions -----
    by_content: dict[str, int] = {}
    for index, hit in enumerate(hits):
        payload = getattr(hit, "payload", None) or {}
        content_hash = payload.get("content_sha256")
        if not content_hash:
            continue
        previous = by_content.get(content_hash)
        if previous is not None:
            union(previous, index)
        else:
            by_content[content_hash] = index

    # ----- pass 2: dHash hamming-distance unions, bucketed by width -----
    dhash_groups: dict[int, list[tuple[int, int]]] = {}
    for index, hit in enumerate(hits):
        payload = getattr(hit, "payload", None) or {}
        dhash = payload.get("dhash")
        if not dhash or not isinstance(dhash, str):
            continue
        # Use len*4 for the bucket key. `int(hex_str, 16).bit_length()`
        # gives a different value for short hashes (e.g. "0f" -> 4 vs
        # len*4=8) and would silently miss cross-bucket unions.
        width = len(dhash) * 4
        try:
            dhash_int = int(dhash, 16)
        except ValueError:
            continue
        dhash_groups.setdefault(width, []).append((dhash_int, index))

    W = 256
    for width, group in dhash_groups.items():  # noqa: B007
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda pair: pair[1])
        sub_indices = np.asarray([idx for _, idx in ordered], dtype=np.int64)
        sub_values = np.asarray([val for val, _ in ordered], dtype=np.uint64)
        m = len(ordered)
        if m < 2:
            continue
        w = min(W, m - 1)
        # right_vals[i, k] = sub_values[i + k + 1], 0 elsewhere.
        # Zero padding matters: xor with 0 keeps the original value,
        # popcount stays as-is for out-of-window positions. We zero
        # those out below via the `valid` mask.
        right_vals = np.zeros((m, w), dtype=np.uint64)
        kk = np.arange(w)
        ar = np.arange(m)[:, None] + kk + 1
        valid = ar < m
        right_vals[valid] = sub_values[ar[valid]]
        left_vals = np.broadcast_to(sub_values[:, None], (m, w)).copy()
        xor_band = left_vals ^ right_vals
        # Popcount via byte-level unpack: 64 bits -> 8 bytes per cell,
        # unpack to 64 bits, sum. numpy 2.x has no np.bit_count ufunc.
        xor_bytes = np.ascontiguousarray(xor_band).view(np.uint8).reshape(m, w, 8)
        ham = np.unpackbits(xor_bytes, axis=-1).sum(axis=-1, dtype=np.int8)
        for a, k in zip(*np.where(valid & (ham <= duplicate_hamming_distance)), strict=True):
            left_index = int(sub_indices[a])
            right_index = int(sub_indices[a + int(k) + 1])
            union(left_index, right_index)

    # Pick the highest-relevance representative per group.
    groups: dict[int, list[int]] = {}
    for index in range(len(hits)):
        groups.setdefault(find(index), []).append(index)
    representatives: list[int] = []
    for members in groups.values():
        best = max(members, key=lambda i: float(query_scores[i]))
        representatives.append(best)
    representatives.sort()
    return representatives


# ---------------------------------------------------------------------------
# Relevance floor (post-MMR filter)
# ---------------------------------------------------------------------------


def apply_relevance_floor(
    hits: list,
    *,
    query_scores,
    floor: float,
    min_results: int = 1,
) -> list[int]:
    """Drop hits whose score is more than `floor` below the top score.

    Returns the indices of hits that pass the floor. If the floor
    would empty the result, returns the top-`min_results` indices
    instead (so a degenerate candidate pool still produces something
    for the caller).

    `floor == 0.0` (the default) is a no-op and returns `range(len(hits))`.
    """
    if not hits:
        return []
    if floor <= 0.0:
        return list(range(len(hits)))
    scores = [float(s) for s in query_scores]
    top_score = max(scores)
    threshold = top_score * (1.0 - floor)
    keep = [i for i, s in enumerate(scores) if s >= threshold]
    if len(keep) < min_results:
        # Floor wiped everything (degenerate input); fall back to top-N
        # by score so the caller doesn't get an empty page.
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return order[:max(min_results, 1)]
    return keep
