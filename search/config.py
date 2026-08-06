"""
search/config.py — environment variable loading + validation.

Loaded once at process start, validated up-front, frozen for the
lifetime of the process. See .env.example for the full table.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env from cwd (or any ancestor) on import. Real process env wins.
load_dotenv()

DEFAULT_MODEL: str = "ViT-gopt-16-SigLIP2-384"
DEFAULT_COLLECTION: str = "images"

# Mapping from open_clip arch tag → (centroid-file `model` string,
# expected feature dim). The centroid's `model` field is a short
# lowercase tag written by `isaac-image-scoring`; the search side's
# MODEL_NAME is the open_clip arch tag. We map between them so the
# store can refuse to load a centroid that lives in a different
# embedding space than the indexed images.
#
# Add new entries here when the model changes. The unknown-model
# branch raises at config load time — fail fast rather than serve
# garbage cosine results.
_CENTROID_MODEL_COMPAT = {
    "ViT-gopt-16-SigLIP2-384": ("siglip2", 1536),
}


def centroid_compat_for(model_name: str) -> tuple[str, int]:
    """
    Return (expected_model_tag, expected_feature_dim) for the
    given open_clip arch tag. Raises ValueError on unknown models
    so the search container fails to start with a clear error
    rather than silently loading mismatched centroids.
    """
    if model_name not in _CENTROID_MODEL_COMPAT:
        raise ValueError(
            f"MODEL_NAME={model_name!r} has no centroid-compat mapping. "
            f"Add one in search/config.py _CENTROID_MODEL_COMPAT. "
            f"Known models: {sorted(_CENTROID_MODEL_COMPAT)}"
        )
    return _CENTROID_MODEL_COMPAT[model_name]


@dataclass(frozen=True)
class Config:
    qdrant_url: str
    qdrant_collection: str
    qdrant_api_key: str | None
    model_name: str
    model_revision: str
    device: str
    top_k_default: int
    top_k_max: int
    query_timeout_ms: int
    nas_images_base: str
    path_prefix: str
    web_ui_url: str
    log_level: str
    # In test mode the real model is replaced with a deterministic mock.
    # Set SEARCH_TEST_MODE=1 from conftest to enable.
    test_mode: bool
    # Surprise Me: fetch a deep pool (no vectors), shuffle, return a
    # small random slice. Pool size controls the diversity-relevance
    # trade-off (bigger = more diverse but slower).
    surprise_pool_size: int = 5000
    surprise_result_count: int = 28
    # Custom centroids: read-only dir of .pt files produced by
    # `isaac-image-scoring`. Optional — when unset or missing, the
    # centroid store is empty and the feature is effectively off.
    # Defaults to None so existing test fixtures (which construct
    # Config directly) keep working unchanged.
    centroids_dir: str | None = None
    # Per-request timeout for the discovery rabbithole's recommend()
    # call. Recommend is heavier than a plain search (Qdrant has to
    # fetch the positive/negative point vectors, compute their mean,
    # then run an HNSW search across the whole collection), and the
    # default 2s used for normal search is too tight over HTTPS
    # through a reverse proxy on a 270K+ point collection. 10s is
    # generous; in practice a healthy Qdrant returns in <1s.
    recommend_timeout_ms: int = 10000
    # Derived from MODEL_NAME: which `model` tag and dim centroids
    # must have to be loaded. Defaults match the production model
    # so the centroid-compat guard is meaningful out of the box;
    # production always sets these via config.load().
    centroid_expected_model: str = "siglip2"
    centroid_expected_feature_dim: int = 1536
    index_db_path: str = "./data/images.db"
    # ----- Operational constants (formerly module-level in app.py / discover.py) -----
    # All env-driven so an operator can tune the running service without
    # a code change. Defaults match the prior hardcoded values exactly.
    max_results_total: int = 5000
    static_assets_version: int = 22
    max_prompt_chars: int = 512
    max_prompts_total: int = 16
    # `valid_views` and `default_view` are a closed enum; not env-driven.
    # Moved to Config for testability (tests can construct a Config with
    # custom values instead of monkey-patching module globals).
    valid_views: tuple[str, ...] = ("grid", "feed")
    default_view: str = "grid"
    # FTS filter cardinality guard (see app.py:_resolve_filename_filter).
    filename_cardinality_guard: float = 0.5
    # Discovery rabbithole burst timeline (formerly module-level in
    # discover.py). Tuned empirically against a real dataset; these
    # are exactly the knobs an operator wants to fiddle with in prod.
    discover_seed_rounds: int = 10
    discover_recommend_overfetch: int = 200
    discover_diversify_lambda: float = 0.5
    discover_mmr_pool_size: int = 10
    discover_burst_size: int = 5
    discover_session_ttl_seconds: int = 1800
    # ----- Dual-store sync (Qdrant ↔ SQLite IndexDB) -----
    # How often the search container re-runs `IndexDB.init_from_qdrant`
    # in the background so the browse cache (SQLite) catches up with
    # bulk indexer runs without an operator hitting
    # POST /api/cache/refresh. Manual refresh still works as a
    # force-now override. Default 6h is a sweet spot: long enough to
    # not waste Qdrant scroll bandwidth, short enough that /random
    # and /albums are rarely more than 6h stale.
    index_db_refresh_interval_seconds: int = 21600
    # TTL for the lazy path-liveness cache (see `app.py:_is_path_alive`).
    # Bounds per-request `Path.exists()` cost while keeping the read
    # path fresh. 60s means a freshly-deleted file shows up as dead
    # within a minute; tune higher if you're on a slow NAS.
    path_liveness_ttl_seconds: int = 60


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"env {name}={raw!r} is not a valid int") from e


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"env {name}={raw!r} is not a valid float") from e


def load() -> Config:
    """
    Load config from environment. Validates required fields and
    invariants. Raises ValueError on invalid input.
    """
    nas_base = os.environ.get("NAS_IMAGES_BASE", "")
    if not nas_base and not os.environ.get("SEARCH_TEST_MODE"):
        # In test mode the NAS base may be a fixture path, set by conftest.
        raise ValueError("NAS_IMAGES_BASE is required")

    top_k_default = _int("TOP_K_DEFAULT", 70)
    top_k_max = _int("TOP_K_MAX", 200)
    if not (1 <= top_k_default <= top_k_max):
        raise ValueError(
            f"TOP_K_DEFAULT={top_k_default} must be in [1, TOP_K_MAX={top_k_max}]"
        )

    expected_model, expected_dim = centroid_compat_for(
        os.environ.get("MODEL_NAME", DEFAULT_MODEL)
    )

    index_db_path = os.environ.get("INDEX_DB_PATH")
    if not index_db_path and os.environ.get("SEARCH_TEST_MODE"):
        index_db_path = ":memory:"
    if not index_db_path:
        index_db_path = "./data/images.db"

    cfg = Config(
        qdrant_url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        qdrant_collection=os.environ.get("QDRANT_COLLECTION", DEFAULT_COLLECTION),
        qdrant_api_key=os.environ.get("QDRANT_API_KEY") or None,
        model_name=os.environ.get("MODEL_NAME", DEFAULT_MODEL),
        model_revision=os.environ.get("MODEL_REVISION", ""),
        device=os.environ.get("DEVICE", "cpu"),
        top_k_default=top_k_default,
        top_k_max=top_k_max,
        query_timeout_ms=_int("QUERY_TIMEOUT_MS", 30000),
        recommend_timeout_ms=_int("RECOMMEND_TIMEOUT_MS", 10000),
        nas_images_base=nas_base,
        path_prefix=os.environ.get("PATH_PREFIX", ""),
        web_ui_url=os.environ.get("WEB_UI_URL", "http://localhost:8000"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        test_mode=bool(os.environ.get("SEARCH_TEST_MODE")),
        centroids_dir=os.environ.get("CENTROIDS_DIR") or None,
        centroid_expected_model=expected_model,
        centroid_expected_feature_dim=expected_dim,
        index_db_path=index_db_path,
        max_results_total=_int("MAX_RESULTS_TOTAL", 5000),
        static_assets_version=_int("STATIC_ASSETS_VERSION", 22),
        max_prompt_chars=_int("MAX_PROMPT_CHARS", 512),
        max_prompts_total=_int("MAX_PROMPTS_TOTAL", 16),
        filename_cardinality_guard=_float("FILENAME_CARDINALITY_GUARD", 0.5),
        discover_seed_rounds=_int("DISCOVER_SEED_ROUNDS", 10),
        discover_recommend_overfetch=_int("DISCOVER_RECOMMEND_OVERFETCH", 200),
        discover_diversify_lambda=_float("DISCOVER_DIVERSIFY_LAMBDA", 0.5),
        discover_mmr_pool_size=_int("DISCOVER_MMR_POOL_SIZE", 10),
        discover_burst_size=_int("DISCOVER_BURST_SIZE", 5),
        discover_session_ttl_seconds=_int("DISCOVER_SESSION_TTL_SECONDS", 1800),
        index_db_refresh_interval_seconds=_int("INDEX_DB_REFRESH_INTERVAL_SECONDS", 21600),
        path_liveness_ttl_seconds=_int("PATH_LIVENESS_TTL_SECONDS", 60),
    )

    # Validate NAS base if set (test mode may set it later).
    if cfg.nas_images_base and not Path(cfg.nas_images_base).is_dir():
        raise ValueError(
            f"NAS_IMAGES_BASE does not exist or is not a directory: {cfg.nas_images_base}"
        )

    return cfg
