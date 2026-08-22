"""
image_search_kernel.payload_schema

Canonical Qdrant point payload schema. Single source of truth for
every field name and type stored on a Qdrant point.

Moved from `indexer/schema.py` so that both `search/` and `indexer/`
import the same constants. The `Payload` TypedDict lives here; the
writer (`indexer/upsert.py:build_payload`) and the readers (`search/*`)
both import from here, so a typo in one place can't silently break the
other side.

Adding a field:
  1. Add the constant below.
  2. Extend `Payload` TypedDict with the matching key/type.
  3. Update `SCHEMA.md` (the prose mirror at the repo root).
  4. If readers need it, add the constant import to the consumer.

Renaming or removing a field is a breaking change to the on-disk
collection. Coordinate a backfill / migration before shipping.

Schema versioning
-----------------

The Qdrant collection carries a `_schema_version` field on every
point. The current version is `1` and is set by the kernel as
`SCHEMA_VERSION`. Every writer must set it; every reader must check
it; readers refuse unknown versions (HTTP 503 with structured body,
see `docs/backend-refactor-plan.md` §4.2.1).
"""

from __future__ import annotations

from typing import TypedDict

__all__ = [  # noqa: RUF022 — grouped by category for readability; strict alphabetic sort would scatter related fields
    # Schema version
    "SCHEMA_VERSION",
    # Top-level payload fields
    "FIELD_ID",
    "FIELD_PATH",
    "FIELD_SHARD",
    "FIELD_COLLECTION",
    "FIELD_BLURHASH",
    "FIELD_FOLDER",
    "FIELD_MTIME",
    "FIELD_SIZE",
    "FIELD_MODEL_NAME",
    "FIELD_MODEL_REVISION",
    "FIELD_MODEL_DIM",
    "FIELD_INDEXED_AT",
    "FIELD_SCHEMA_VERSION",
    # Diversity fingerprints
    "FIELD_CONTENT_SHA256",
    "FIELD_DHASH",
    "FINGERPRINT_FIELDS",
    # TypedDict
    "Payload",
    # Helpers
    "payload_field_names",
    "require_fields",
]


# Schema versioning. v0 = pre-versioned (legacy); v1 = first versioned
# schema, introduced by the refactor. New versions bump this constant
# and add a migration transform.
SCHEMA_VERSION: int = 1


# --- Top-level payload fields --------------------------------------------

FIELD_ID = "id"
FIELD_PATH = "path"
FIELD_SHARD = "shard"
FIELD_COLLECTION = "collection"
FIELD_BLURHASH = "blurhash"
FIELD_FOLDER = "folder"
FIELD_MTIME = "mtime"
FIELD_SIZE = "size"
FIELD_MODEL_NAME = "model_name"
FIELD_MODEL_REVISION = "model_revision"
FIELD_MODEL_DIM = "model_dim"
FIELD_INDEXED_AT = "indexed_at"
FIELD_SCHEMA_VERSION = "_schema_version"


# --- Diversity fingerprints (sub-keys; same flat payload) ---------------

FIELD_CONTENT_SHA256 = "content_sha256"
FIELD_DHASH = "dhash"

# All fingerprint keys in one place so callers can iterate without
# listing them by hand.
FINGERPRINT_FIELDS: tuple[str, ...] = (FIELD_CONTENT_SHA256, FIELD_DHASH)


# --- TypedDict describing the full point payload ------------------------

class Payload(TypedDict, total=False):
    """Canonical Qdrant point payload (Qdrant stores payload as JSON).

    All fields are optional in the type system because legacy points
    written before the field was introduced may omit it. The
    `require_fields` helper below asserts the required set at parse
    time; failure raises a typed exception.

    `_schema_version` is required for any point written by a versioned
    writer. Readers must check it and refuse unknown versions.
    """
    _schema_version: int
    id: str
    path: str
    shard: str
    collection: str
    blurhash: str | None
    folder: str
    mtime: int
    size: int
    model_name: str
    model_revision: str
    model_dim: int
    indexed_at: str
    # Diversity fingerprints (flat, not nested)
    content_sha256: str | None
    dhash: str | None


# --- Required fields per schema version ----------------------------------

# v0 (pre-versioned, legacy before the refactor): only the field set
# that existed before _schema_version was introduced.
REQUIRED_FIELDS_V0: frozenset[str] = frozenset({
    FIELD_ID,
    FIELD_PATH,
})

# v1: first versioned schema. Adds _schema_version, folder, model_dim.
REQUIRED_FIELDS_V1: frozenset[str] = frozenset({
    FIELD_ID,
    FIELD_PATH,
    FIELD_FOLDER,
    FIELD_MODEL_NAME,
    FIELD_MODEL_REVISION,
    FIELD_MODEL_DIM,
    FIELD_SCHEMA_VERSION,
})


def payload_field_names() -> list[str]:
    """Sorted list of every payload field name declared in this module.

    Used by `tests/test_schema.py` to cross-check that `SCHEMA.md`
    mentions every field.
    """
    return sorted({
        FIELD_ID,
        FIELD_PATH,
        FIELD_SHARD,
        FIELD_COLLECTION,
        FIELD_BLURHASH,
        FIELD_FOLDER,
        FIELD_MTIME,
        FIELD_SIZE,
        FIELD_MODEL_NAME,
        FIELD_MODEL_REVISION,
        FIELD_MODEL_DIM,
        FIELD_INDEXED_AT,
        FIELD_SCHEMA_VERSION,
        FIELD_CONTENT_SHA256,
        FIELD_DHASH,
    })


def require_fields(payload: dict[str, object], *, version: int) -> None:
    """Assert that `payload` contains every required field for its version.

    Raises:
        ValueError: with a structured message naming the missing fields
            and the version that required them.
    """
    if version == 0:
        required = REQUIRED_FIELDS_V0
    elif version == 1:
        required = REQUIRED_FIELDS_V1
    else:
        raise ValueError(f"unknown schema version: {version!r}")

    missing = [f for f in required if f not in payload]
    if missing:
        raise ValueError(
            f"payload missing required field(s) {missing!r} for schema version {version}"
        )
