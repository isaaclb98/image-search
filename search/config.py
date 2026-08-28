"""
search/config.py — environment variable loading + validation.

Loaded once at process start, validated up-front, frozen for the
lifetime of the process. See .env.example for the full table.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from search.diversity_config import Diversity, load_diversity_from_env

logger = logging.getLogger(__name__)

# Load .env from cwd (or any ancestor) on import. Real process env wins.
load_dotenv()

# SigLIP2 variant mapping: variant name -> (model name, vector dimension)
SIGLIP_VARIANTS = {
    "B/16-256": ("ViT-B-16-SigLIP2-256", 768),
    "L/16-256": ("ViT-L-16-SigLIP2-256", 1024),
    "gopt/16-384": ("ViT-gopt-16-SigLIP2-384", 1536),
}

DEFAULT_VARIANT = "L/16-256"  # 1024-dim, balanced quality/speed

def get_siglip_variant() -> str:
    """Get the configured SigLIP2 variant from SIGLIP_VARIANT env var."""
    variant = os.environ.get("SIGLIP_VARIANT", DEFAULT_VARIANT)
    if variant not in SIGLIP_VARIANTS:
        raise ValueError(
            f"Invalid SIGLIP_VARIANT '{variant}'. "
            f"Must be one of: {', '.join(SIGLIP_VARIANTS.keys())}"
        )
    return variant

def get_model_name_for_variant(variant: str) -> str:
    """Get the model name for a given variant."""
    if variant not in SIGLIP_VARIANTS:
        raise ValueError(f"Unknown variant: {variant}")
    return SIGLIP_VARIANTS[variant][0]

def get_vector_dim_for_variant(variant: str) -> int:
    """Get the vector dimension for a given variant."""
    if variant not in SIGLIP_VARIANTS:
        raise ValueError(f"Unknown variant: {variant}")
    return SIGLIP_VARIANTS[variant][1]

def get_vector_dim() -> int:
    """Get the vector dimension for the currently configured variant."""
    return get_vector_dim_for_variant(get_siglip_variant())


# Thumbnail storage path (inside container)
THUMBNAIL_DIR = os.environ.get("THUMBNAIL_DIR", "/app/data/thumbnails")


# Variant storage: JSON file in the data directory
VARIANT_CONFIG_FILE = "siglip_variant.json"


def get_variant_config_path(data_dir: str = "./data") -> Path:
    """Get the path to the variant config file."""
    return Path(data_dir) / VARIANT_CONFIG_FILE


def load_stored_variant(data_dir: str = "./data") -> str | None:
    """Load the stored variant from the config file, or None if not found."""
    config_path = get_variant_config_path(data_dir)
    if not config_path.exists():
        return None
    try:
        import json
        with open(config_path) as f:
            data = json.load(f)
            return data.get("variant")
    except (OSError, ValueError) as e:
        # JSON decode errors are ValueError subclasses; file
        # permission/missing errors are OSError. Anything else is a
        # real bug — let it propagate.
        logger.warning("Failed to load variant config from %s: %s", config_path, e)
        return None


def save_variant(variant: str, data_dir: str = "./data") -> None:
    """Save the variant to the config file."""
    import json
    config_path = get_variant_config_path(data_dir)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump({"variant": variant}, f)
    logger.info("Saved variant '%s' to %s", variant, config_path)


def validate_variant_against_stored(env_variant: str, data_dir: str = "./data") -> None:
    """
    Validate that the env variant matches the stored variant.
    Raises ValueError with a clear message if there's a mismatch.
    """
    stored = load_stored_variant(data_dir)
    if stored is None:
        # First run or no config file — save the current variant
        save_variant(env_variant, data_dir)
        logger.info("First run: stored variant '%s'", env_variant)
        return
    
    if stored != env_variant:
        stored_model = get_model_name_for_variant(stored)
        stored_dim = get_vector_dim_for_variant(stored)
        env_model = get_model_name_for_variant(env_variant)
        env_dim = get_vector_dim_for_variant(env_variant)
        
        raise ValueError(
            f"Model variant mismatch!\n"
            f"  Stored: {stored} ({stored_model}, {stored_dim}-dim)\n"
            f"  Env:    {env_variant} ({env_model}, {env_dim}-dim)\n"
            f"\n"
            f"Changing the model variant requires re-indexing all photos.\n"
            f"Either:\n"
            f"  - Remove the variant config: rm {get_variant_config_path(data_dir)}\n"
            f"    (then drop the Qdrant collection + reindex from scratch),\n"
            f"  - Or revert SIGLIP_VARIANT to {stored!r} so it matches the\n"
            f"    already-indexed embeddings.\n"
        )
    
    logger.info("Variant validated: %s", env_variant)

# Backward compatibility: these are derived from the variant
DEFAULT_MODEL: str = get_model_name_for_variant(get_siglip_variant())
DEFAULT_COLLECTION: str = "images"
# Round‑14: separate read/write collections so the indexer never
# contends with the app. The indexer writes to `images_pending`;
# the app reads from `images`; a background SyncManager moves
# pending → images in small batches.
DEFAULT_WRITE_COLLECTION: str = "images_pending"
SYNC_BATCH_SIZE: int = 100
SYNC_INTERVAL_SECONDS: float = 5.0
DEFAULT_RESULT_LIMIT: int = 20

# Mapping from open_clip arch tag → (centroid-file `model` string).
# The expected feature dim is read from the model registry at lookup
# time, not stored here — the registry is the single source of truth.
# The centroid's `model` field is a short lowercase tag written by
# `isaac-image-scoring`; the search side's MODEL_NAME is the open_clip
# arch tag. We map between them so the store can refuse to load a
# centroid that lives in a different embedding space than the indexed
# images.
#
# Add new entries here only when introducing a new model family.
# The unknown-model branch raises at config load time — fail fast
# rather than serve garbage cosine results.
_CENTROID_MODEL_COMPAT = {
    "ViT-gopt-16-SigLIP2-384": "siglip2",
    "ViT-L-16-SigLIP2-256": "siglip2",
}


def centroid_compat_for(model_name: str) -> tuple[str, int]:
    """
    Return (expected_model_tag, expected_feature_dim) for the
    given open_clip arch tag. Raises ValueError on unknown models
    so the search container fails to start with a clear error
    rather than silently loading mismatched centroids.

    `expected_feature_dim` is sourced from the model registry by
    `model_name`; the registry is the only place model dimensions
    are referenced.

    open_clip tags carry an "hf-hub:<vendor>/" prefix (e.g.
    ``hf-hub:timm/ViT-gopt-16-SigLIP2-384``); the map is keyed by
    the bare arch tag. Normalize by splitting on ``/`` and taking
    the last segment so the deployment's MODEL_NAME matches
    regardless of how open_clip names the model.
    """
    bare = model_name.split("/")[-1] if "/" in model_name else model_name
    if bare not in _CENTROID_MODEL_COMPAT:
        raise ValueError(
            f"MODEL_NAME={model_name!r} has no centroid-compat mapping. "
            f"Add one in search/config.py _CENTROID_MODEL_COMPAT. "
            f"Known models: {sorted(_CENTROID_MODEL_COMPAT)}"
        )
    from image_search_kernel.registry import get as _registry_get
    return _CENTROID_MODEL_COMPAT[bare], _registry_get(bare).dim


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
    # Round‑14: separate read/write collections so the indexer never
    # contends with the app. The indexer writes to `qdrant_write_collection`;
    # the app reads from `qdrant_collection`; a background SyncManager
    # moves pending → search in small batches.
    qdrant_write_collection: str = DEFAULT_WRITE_COLLECTION
    qdrant_sync_batch_size: int = SYNC_BATCH_SIZE
    qdrant_sync_interval_seconds: float = SYNC_INTERVAL_SECONDS
    # Search Diversity. These knobs apply only to ordinary /api/search and
    # the SSR search page.
    diversity_max_candidate_pool_size: int = 5000
    diversity_cache_ttl_seconds: int = 300
    diversity_cache_max_entries: int = 64
    diversity_duplicate_hamming_distance: int = 10
    diversity_relevance_drop: float = 0.10
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
    # Single source of truth for the Diversity knob. Routers resolve
    # query params against this default via `resolve_diversity()`.
    diversity: Diversity = field(default_factory=load_diversity_from_env)
    # Per-request timeout for Qdrant's recommend() API (used by /api/for-you's
    # centroid blend and any future recommend-style endpoints). Recommend
    # is heavier than a plain search (Qdrant has to fetch positive/negative
    # point vectors, compute their mean, then run an HNSW search across
    # the whole collection), and the default 2s used for normal search is
    # too tight over HTTPS through a reverse proxy on a 270K+ point
    # collection. 10s is generous; in practice a healthy Qdrant returns
    # in <1s.
    recommend_timeout_ms: int = 10000
    # Derived from MODEL_NAME: which `model` tag and dim centroids
    # must have to be loaded. Defaults match the production model
    # so the centroid-compat guard is meaningful out of the box;
    # production always sets these via config.load().
    centroid_expected_model: str = "siglip2"
    # Sourced from the model registry by `config.load()`. Default
    # pulls from the registry so tests that construct `Config()`
    # directly (without going through `config.load()`) still see a
    # real dim. Production always overrides via `centroid_compat_for()`.
    centroid_expected_feature_dim: int = field(
        default_factory=lambda: __import__(
            "image_search_kernel.registry", fromlist=["get"],
        ).get("ViT-gopt-16-SigLIP2-384").dim,
    )
    index_db_path: str = "./data/images.db"
    # ----- Operational constants (formerly module-level in app.py) -----
    # All env-driven so an operator can tune the running service without
    # a code change. Defaults match the prior hardcoded values exactly.
    max_results_total: int = 5000
    static_assets_version: int = 32
    max_prompt_chars: int = 512
    max_prompts_total: int = 16
    # `valid_views` and `default_view` are a closed enum; not env-driven.
    # Moved to Config for testability (tests can construct a Config with
    # custom values instead of monkey-patching module globals).
    valid_views: tuple[str, ...] = ("grid", "feed")
    default_view: str = "grid"
    # FTS filter cardinality guard (see app.py:_resolve_filename_filter).
    filename_cardinality_guard: float = 0.5
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
    # (Single-user auth removed. Front the service with a reverse-proxy
    # auth if access control is needed: caddy, oauth2-proxy, tailscale, etc.)


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


def _bool(name: str, default: bool) -> bool:
    """Parse a boolean env var. Truthy: 1/true/yes/on (case-insensitive)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load() -> Config:
    """
    Load config from environment. Validates required fields and
    invariants. Raises ValueError on invalid input.
    """
    nas_base = os.environ.get("NAS_IMAGES_BASE", "")
    if not nas_base and not os.environ.get("SEARCH_TEST_MODE"):
        # In test mode the NAS base may be a fixture path, set by conftest.
        raise ValueError("NAS_IMAGES_BASE is required")

    # Validate SigLIP2 variant before loading the rest of the config
    variant = get_siglip_variant()
    index_db_path = os.environ.get("INDEX_DB_PATH")
    if not index_db_path and os.environ.get("SEARCH_TEST_MODE"):
        index_db_path = ":memory:"
    if not index_db_path:
        index_db_path = "./data/images.db"
    
    # Determine data directory from index_db_path
    data_dir = "./data" if index_db_path == ":memory:" else str(Path(index_db_path).parent)
    
    # Validate variant against stored config (raises on mismatch)
    validate_variant_against_stored(variant, data_dir)

    top_k_default = _int("TOP_K_DEFAULT", DEFAULT_RESULT_LIMIT)
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
        qdrant_collection=os.environ.get("QDRANT_READ_COLLECTION", DEFAULT_COLLECTION),
        qdrant_write_collection=os.environ.get("QDRANT_WRITE_COLLECTION", DEFAULT_WRITE_COLLECTION),
        qdrant_sync_batch_size=_int("QDRANT_SYNC_BATCH_SIZE", SYNC_BATCH_SIZE),
        qdrant_sync_interval_seconds=_float("QDRANT_SYNC_INTERVAL_SECONDS", SYNC_INTERVAL_SECONDS),
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
        test_mode=bool(
            os.environ.get("SEARCH_TEST_MODE")
            or os.environ.get("SEARCH_NO_MODEL")
        ),
        diversity_max_candidate_pool_size=_int("DIVERSITY_MAX_CANDIDATE_POOL_SIZE", 5000),
        diversity_cache_ttl_seconds=_int("DIVERSITY_CACHE_TTL_SECONDS", 300),
        diversity_cache_max_entries=_int("DIVERSITY_CACHE_MAX_ENTRIES", 64),
        diversity_duplicate_hamming_distance=_int(
            "DIVERSITY_DUPLICATE_HAMMING_DISTANCE", 10
        ),
        diversity_relevance_drop=_float("DIVERSITY_RELEVANCE_DROP", 0.10),
        centroids_dir=os.environ.get("CENTROIDS_DIR") or None,
        centroid_expected_model=expected_model,
        centroid_expected_feature_dim=expected_dim,
        index_db_path=index_db_path,
        max_results_total=_int("MAX_RESULTS_TOTAL", 5000),
        static_assets_version=_int("STATIC_ASSETS_VERSION", 32),
        max_prompt_chars=_int("MAX_PROMPT_CHARS", 512),
        max_prompts_total=_int("MAX_PROMPTS_TOTAL", 16),
        filename_cardinality_guard=_float("FILENAME_CARDINALITY_GUARD", 0.5),
        index_db_refresh_interval_seconds=_int("INDEX_DB_REFRESH_INTERVAL_SECONDS", 21600),
        path_liveness_ttl_seconds=_int("PATH_LIVENESS_TTL_SECONDS", 60),
        # Auth removed — no env vars to read here.
    )

    if cfg.diversity_max_candidate_pool_size < cfg.top_k_default:
        raise ValueError(
            "DIVERSITY_MAX_CANDIDATE_POOL_SIZE must be >= TOP_K_DEFAULT"
        )
    if cfg.diversity_cache_ttl_seconds < 0 or cfg.diversity_cache_max_entries < 1:
        raise ValueError("Diversity cache settings must be non-negative and non-empty")
    if not 0 <= cfg.diversity_duplicate_hamming_distance <= 64:
        raise ValueError(
            "DIVERSITY_DUPLICATE_HAMMING_DISTANCE must be between 0 and 64"
        )
    if not math.isfinite(cfg.diversity_relevance_drop) or cfg.diversity_relevance_drop < 0:
        raise ValueError("DIVERSITY_RELEVANCE_DROP must be finite and >= 0")

    # Validate NAS base if set (test mode may set it later).
    if cfg.nas_images_base and not Path(cfg.nas_images_base).is_dir():
        raise ValueError(
            f"NAS_IMAGES_BASE does not exist or is not a directory: {cfg.nas_images_base}"
        )

    return cfg
