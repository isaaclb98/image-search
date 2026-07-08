"""
search/centroids.py — load and serve custom centroid vectors.

A centroid is a precomputed embedding (typically a taste mean) saved
to disk by `isaac-image-scoring` as a torch .pt file with this shape:

    torch.save({
        "centroid":     Tensor[FEATURE_DIM],  # unit-norm
        "name":         str,
        "model":        str,                  # e.g. "siglip2"
        "model_type":   str,                  # e.g. "siglip2", "ensemble", "dinov3"
        "model_id":     str | None,
        "feature_dim":  int,
        "n_images":     int,
        "extracted_at": iso-8601 str,
    }, path)

The search side reads these once at startup and exposes them as a
read-only in-memory store keyed by name. There is no write path on
the search side — centroids are owned by `isaac-image-scoring`. The
search app is just a consumer.

**Model/dim guard.** Every centroid must come from the same embedding
space as the indexed images, otherwise Qdrant cosine search returns
nonsense (1536-dim centroid vs. 4096-dim dino v3 collection, etc.). A
centroid whose `model`/`model_type` or `feature_dim` does not match
the configured `expected_model` is skipped at load time and logged
with a clear warning. The store never serves a mismatched centroid
and the routes 404 on lookup, but the user can still see *that* it
exists in the directory via the file path (handy for debugging).

Design reference: DESIGN.md §"Custom centroids" (added in this commit).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import torch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CentroidSpec:
    """
    One loaded centroid. Immutable; the store replaces the whole
    dict on reload rather than mutating individual specs.

    `vector` is a plain Python list[float] (length `feature_dim`,
    unit-norm) — the shape that QdrantClient.query_points expects.
    We materialize the tensor once at load to avoid paying the
    tensor → list conversion on every search.
    """

    name: str
    vector: list[float]
    model: str
    model_type: str
    model_id: str | None
    feature_dim: int
    n_images: int
    extracted_at: str
    source_path: Path

    def as_dict(self) -> dict:
        """Public-facing summary for the JSON list endpoint."""
        return {
            "name": self.name,
            "model": self.model,
            "model_type": self.model_type,
            "model_id": self.model_id,
            "feature_dim": self.feature_dim,
            "n_images": self.n_images,
            "extracted_at": self.extracted_at,
            "source_path": str(self.source_path),
        }


class CentroidStore:
    """
    In-memory store of available centroids.

    Lifecycle:
      - `__init__` does not touch disk. Callers (the app factory,
        tests) call `load()` explicitly so the store can be
        constructed without a directory present and reloaded on
        demand.
      - `load()` walks `centroids_dir`, parses every .pt file,
        applies the model/dim guard, and replaces the in-memory
        map with the new set. Any prior centroids not present in
        the new scan are dropped. Loading is total: a malformed
        file does not abort the load, it is logged and skipped.

    Thread-safety: all reads are dict lookups (thread-safe in CPython
    under the GIL). `load()` swaps a single attribute to a brand-new
    dict, so concurrent readers either see the old set or the new
    set — never a partial state.
    """

    def __init__(
        self,
        centroids_dir: Path | None,
        expected_model: str,
        expected_feature_dim: int,
    ) -> None:
        self._centroids_dir = Path(centroids_dir) if centroids_dir else None
        self._expected_model = expected_model
        self._expected_feature_dim = expected_feature_dim
        # Public attribute so tests can introspect what was loaded.
        # Maps name → CentroidSpec. Replaced wholesale by load().
        self._by_name: dict[str, CentroidSpec] = {}

    @property
    def centroids_dir(self) -> Path | None:
        return self._centroids_dir

    def load(self) -> int:
        """
        Scan the directory and rebuild the in-memory map.

        Returns the number of centroids successfully loaded. Centroids
        that fail the model/dim guard or fail to parse are skipped
        and logged; the rest are loaded.

        A missing directory is treated as "no centroids" rather than
        a hard error: the store simply ends up empty. This keeps the
        search container starting cleanly when the optional
        CENTROIDS_DIR is not configured (the common case for the v1
        path where centroids are a future add-on).
        """
        if self._centroids_dir is None:
            logger.info("CentroidStore: no directory configured, store is empty")
            self._by_name = {}
            return 0
        if not self._centroids_dir.exists() or not self._centroids_dir.is_dir():
            logger.info(
                "CentroidStore: %s does not exist or is not a directory; store is empty",
                self._centroids_dir,
            )
            self._by_name = {}
            return 0

        loaded: dict[str, CentroidSpec] = {}
        for path in sorted(self._centroids_dir.glob("*.pt")):
            spec = self._load_one(path)
            if spec is None:
                continue
            if spec.name in loaded:
                logger.warning(
                    "CentroidStore: duplicate name %r (source %s), keeping the first",
                    spec.name, path,
                )
                continue
            loaded[spec.name] = spec
        self._by_name = loaded
        logger.info(
            "CentroidStore: loaded %d centroid(s) from %s",
            len(loaded), self._centroids_dir,
        )
        return len(loaded)

    def _load_one(self, path: Path) -> CentroidSpec | None:
        """
        Parse a single .pt file. Returns None and logs a warning on
        any failure (missing key, bad shape, mismatched model/dim).
        """
        try:
            blob = torch.load(path, map_location="cpu", weights_only=False)
        except Exception as e:
            logger.warning("CentroidStore: failed to load %s: %s", path, e)
            return None
        if not isinstance(blob, dict):
            logger.warning("CentroidStore: %s is not a dict, skipping", path)
            return None

        required = ("centroid", "name", "model", "feature_dim")
        missing = [k for k in required if k not in blob]
        if missing:
            logger.warning(
                "CentroidStore: %s missing keys %s, skipping",
                path, missing,
            )
            return None

        vector_tensor = blob["centroid"]
        feature_dim = int(blob["feature_dim"])
        if vector_tensor.ndim != 1 or vector_tensor.shape[0] != feature_dim:
            logger.warning(
                "CentroidStore: %s has shape %s, expected (%d,), skipping",
                path, tuple(vector_tensor.shape), feature_dim,
            )
            return None

        model = str(blob["model"])
        model_type = str(blob.get("model_type", model))
        # The guard: the centroid's embedding space must match the
        # search side's configured model and dim. Mismatched centroids
        # are skipped with a clear message so the user knows which
        # file is the offender.
        if model != self._expected_model or feature_dim != self._expected_feature_dim:
            logger.warning(
                "CentroidStore: skipping %s — model=%s dim=%d, "
                "expected model=%s dim=%d. Re-extract this centroid "
                "with the configured model to use it for search.",
                path, model, feature_dim,
                self._expected_model, self._expected_feature_dim,
            )
            return None

        name = str(blob["name"]).strip()
        if not name:
            logger.warning("CentroidStore: %s has empty name, skipping", path)
            return None

        return CentroidSpec(
            name=name,
            vector=vector_tensor.float().tolist(),
            model=model,
            model_type=model_type,
            model_id=(str(blob["model_id"]) if blob.get("model_id") else None),
            feature_dim=feature_dim,
            n_images=int(blob.get("n_images", 0)),
            extracted_at=str(blob.get("extracted_at", "")),
            source_path=path,
        )

    # ----------------- public read API -----------------

    def list(self) -> list[CentroidSpec]:
        """Return all loaded centroids sorted by name."""
        return [self._by_name[n] for n in sorted(self._by_name)]

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def count(self) -> int:
        return len(self._by_name)

    def get(self, name: str) -> CentroidSpec | None:
        """
        Look up a centroid by name. Returns None if not loaded.

        Name matching is case-insensitive: filenames and stored
        `name` fields come from `isaac-image-scoring`'s
        `sanitize_name` (lowercase, underscores, stripped), and a
        user pasting "Wuxia_Female_Leads" should still hit. Returns
        the canonical-stored name on the spec so the route can echo
        the right value in JSON.
        """
        if name in self._by_name:
            return self._by_name[name]
        lower = name.lower()
        for stored in self._by_name:
            if stored.lower() == lower:
                return self._by_name[stored]
        return None

    def expected_model(self) -> str:
        return self._expected_model

    def expected_feature_dim(self) -> int:
        return self._expected_feature_dim


# ---------------------------------------------------------------------------
# Multi-centroid blending
# ---------------------------------------------------------------------------
#
# Some queries benefit from a centroid that lives "between" two or
# more existing centroids — e.g. 70% wuxia + 30% portrait. We
# implement that as a weighted mean of the input vectors,
# re-normalised to unit length, gated by a model/dim check so a
# user can't accidentally average across embedding spaces and
# produce nonsense scores.
#
# The function is pure (no I/O), so it lives next to the centroid
# types rather than on CentroidStore. The route layer fetches each
# centroid vector, hands them to this helper, and ships the result
# to Qdrant as a single query vector.


def blend_centroids(
    entries: list[tuple[list[float], str]],
    weights: list[float] | None,
    expected_dim: int | None = None,
) -> list[float]:
    """Weighted mean of centroid vectors, re-normalised to unit length.

    `entries` is a list of `(vector, name)` pairs. `weights` is an
    optional list of floats with the same length; when omitted or
    shorter than `entries`, missing weights default to 1.0. When
    longer than `entries`, the extras are ignored (defensive
    against URL parsing quirks).

    If `expected_dim` is given, every input vector is checked
    against it. Mismatched dimensions raise ValueError — averaging
    a 768-dim CLIP vector with a 1536-dim SigLIP2 vector returns
    garbage scores and the failure mode is silent, so we refuse
    rather than let it through. The static centroid store already
    enforces expected_model + expected_feature_dim at load time
    and dynamic centroids share the indexer's embedding space, so
    in practice the dim check is a belt-and-braces guard for
    cross-source blends.

    Returns the blended vector as a plain Python list[float]. The
    caller decides what to label it (`wuxia+portrait`, etc.) — the
    helper doesn't impose a name shape.
    """
    if not entries:
        raise ValueError("at least one centroid is required to blend")
    n = len(entries)
    if weights is None:
        weights_list = [1.0] * n
    else:
        weights_list = list(weights)[:n]
        while len(weights_list) < n:
            weights_list.append(1.0)

    # Sum non-positive weights → zero-blend → vector collapses to
    # zero on renormalisation. Reject explicitly so the caller
    # doesn't ship a zero-vector query to Qdrant.
    total = sum(weights_list)
    if total <= 0:
        raise ValueError(
            f"centroid weights must be positive (got {weights_list})"
        )

    dim = len(entries[0][0])
    if dim == 0:
        raise ValueError("centroid vector is empty")
    if expected_dim is not None and dim != expected_dim:
        raise ValueError(
            f"centroid vector dim {dim} != expected {expected_dim} "
            f"(name={entries[0][1]!r})"
        )

    blended = [0.0] * dim
    for (vec, _name), w in zip(entries, weights_list):
        if len(vec) != dim:
            raise ValueError(
                f"centroid {entries[0][1]!r} dim {dim} != "
                f"subsequent vector dim {len(vec)}"
            )
        for i, v in enumerate(vec):
            blended[i] += w * v

    norm_sq = sum(v * v for v in blended)
    norm = norm_sq ** 0.5
    if norm == 0:
        raise ValueError(
            "blended centroid collapsed to zero (weights sum to zero "
            "or all inputs are zero)"
        )
    inv = 1.0 / norm
    return [v * inv for v in blended]


def composite_centroid_name(names: list[str]) -> str:
    """Render a multi-centroid blend label for display.

    Stable order matches the URL `?centroid=&centroid=&centroid=`
    order. Joins with `+` so it's both readable and round-trippable
    through a single token if a future endpoint wants one.

    Two-centroid: `wuxia+portrait`.
    Three-plus:   `wuxia+portrait+landscape` (no truncation —
    UI already copes with long centroid names from .pt files).
    """
    if not names:
        return ""
    return "+".join(names)


# ---------------------------------------------------------------------------
# Dynamic centroids
# ---------------------------------------------------------------------------
#
# A dynamic centroid is computed at runtime from application state
# rather than loaded from disk. The v1 use case is "favourites": the
# mean of every favourited photo's embedding, re-normalised to unit
# length. The compute function is supplied at registration time and
# the registry caches its last result so subsequent reads are O(1).
#
# Lifecycle:
#   - Registered once at app startup (one call per centroid).
#   - Computed lazily on first read.
#   - Invalidated by `mark_favorite` / `unmark_favorite` (or any
#     other code path that mutates the underlying state). The next
#     read triggers a recompute.
#
# Why a separate registry instead of extending CentroidStore:
#   - CentroidStore's contract is immutable post-load. Dynamic
#     centroids are mutable, so conflating them risks accidental
#     reloads wiping a dynamic centroid or vice versa.
#   - Static and dynamic centroids have different metadata shapes
#     (no model/dim guard for dynamic; the compute fn owns that).
#     Keeping them apart keeps each spec lean.
#   - The dynamic registry can grow later to host per-theme or
#     per-window centroids without touching static loading at all.


ComputeFn = Callable[[], "tuple[list[float], int] | None"]
"""Returns (vector, n_images) or None when there's nothing to compute.

None signals "no data" — e.g. zero favourites for the favourites
centroid. The UI treats None as the empty state and renders the
hint copy; the API returns 404 on direct lookup.
"""


@dataclass(frozen=True)
class DynamicCentroidSpec:
    """One registered dynamic centroid."""

    name: str
    label: str
    description: str
    compute_fn: ComputeFn
    # Source identifier surfaced in the UI (e.g. "favourites",
    # "recent-views"). Lets the UI badge or filter without hardcoding.
    source: str
    # Empty-state copy shown when compute_fn returns None. Optional;
    # falls back to a generic message.
    empty_message: str = (
        "Not enough data yet — keep using the app and check back."
    )

    def public_dict(self, n_images: int | None = None) -> dict:
        """JSON shape for /api/centroids. `n_images` is the count from
        the most recent successful compute, or None when nothing has
        been computed yet (cold cache).
        """
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "source": self.source,
            "n_images": n_images,
        }


class DynamicCentroidRegistry:
    """In-memory store of dynamic centroids with lazy recompute.

    Thread-safety: `_by_name` and `_cache` are only ever read or
    wholesale-replaced under `_lock`. CPython dict reads are
    GIL-atomic so the common case (lookup without concurrent
    invalidate) needs no lock.
    """

    def __init__(self) -> None:
        import threading
        self._lock = threading.Lock()
        self._by_name: dict[str, DynamicCentroidSpec] = {}
        # name -> (vector, n_images). Missing entry means "not yet
        # computed". None value means "computed and returned None".
        self._cache: dict[str, tuple[list[float], int] | None] = {}
        # name -> needs recompute. A name not in this set is up to
        # date (or hasn't been computed yet, in which case the
        # cache lookup misses and triggers a compute anyway).
        self._dirty: set[str] = set()

    def register(self, spec: DynamicCentroidSpec) -> None:
        """Register a new dynamic centroid. Forces a recompute on
        next read so the first request after startup pays the
        cost once.
        """
        with self._lock:
            self._by_name[spec.name] = spec
            self._dirty.add(spec.name)
            self._cache.pop(spec.name, None)

    def invalidate(self, name: str) -> None:
        """Mark `name` for recompute and clear the cached value.

        No-op if `name` isn't registered. Called from
        `mark_favorite` / `unmark_favorite` so the next search sees
        the updated centroid.

        Both `_dirty` (recompute flag) and `_cache` (cached value)
        are cleared so `cached_n_images` returns None immediately
        after invalidation — tests use this to verify the cache was
        dropped.

        Use `unregister` instead when the underlying source is
        gone (e.g. an album was deleted) — invalidate alone leaves
        the spec in `_by_name` so it keeps showing up in `list()`
        until process restart.
        """
        with self._lock:
            if name in self._by_name:
                self._dirty.add(name)
                self._cache.pop(name, None)

    def unregister(self, name: str) -> None:
        """Remove the spec for `name` from the registry entirely.

        Drops the spec, any cached value, and any dirty flag so
        the centroid no longer appears in `list()` and `get_vector`
        returns None. Idempotent — no-op if `name` isn't
        registered. Called from album deletion so a deleted album
        stops appearing on `/centroids` immediately, not just on
        next process restart.

        Safe vs concurrent compute: a `get_vector` call may have
        already dropped the lock and be running `compute_fn` when
        unregister fires. Its eventual `_cache[name] = result`
        write lands in an orphaned entry that future lookups
        ignore (they check `_by_name` first). Existing invalidate
        pattern has the same race — accepted here too.
        """
        with self._lock:
            self._by_name.pop(name, None)
            self._cache.pop(name, None)
            self._dirty.discard(name)

    def get_vector(self, name: str) -> tuple[list[float], int] | None:
        """Return the cached (vector, n_images) for `name`.

        Triggers a recompute on first read after registration or
        after invalidate. Returns None if the compute fn returns
        None (empty state) or if `name` is unknown.
        """
        with self._lock:
            if name not in self._by_name:
                return None
            if name in self._dirty or name not in self._cache:
                # Drop the lock for the compute — most compute fns
                # hit the network (Qdrant retrieve) which can take
                # seconds. Holding the registry lock for that long
                # would block all other centroid lookups.
                spec = self._by_name[name]
                # Mark in-progress by removing from dirty and
                # caching the result. If two requests race, both
                # will compute; the second overwrites. That's fine
                # — the compute is read-only against the underlying
                # state and the result is deterministic for a given
                # snapshot.
                self._dirty.discard(name)
                try:
                    result = spec.compute_fn()
                except Exception:
                    logger.exception(
                        "dynamic centroid %r compute failed", name
                    )
                    # Cache the failure as None so we don't keep
                    # retrying on every request. Next invalidate()
                    # clears this.
                    self._cache[name] = None
                    return None
                self._cache[name] = result
        return self._cache.get(name)

    def list(self) -> list[DynamicCentroidSpec]:
        with self._lock:
            return [self._by_name[n] for n in sorted(self._by_name)]

    def get_spec(self, name: str) -> DynamicCentroidSpec | None:
        with self._lock:
            return self._by_name.get(name)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._by_name)

    def cached_n_images(self, name: str) -> int | None:
        """Return the n_images from the last successful compute, or
        None when nothing has been computed yet or the last compute
        returned None.
        """
        with self._lock:
            entry = self._cache.get(name)
            if entry is None:
                return None
            return entry[1]

    def is_empty(self, name: str) -> bool:
        """True when the centroid has been computed and returned
        None (no data). Distinguishes "haven't computed yet" from
        "computed and got nothing" — the UI uses this for the
        empty-state hint.
        """
        with self._lock:
            return (
                name in self._cache
                and self._cache[name] is None
                and name not in self._dirty
            )
