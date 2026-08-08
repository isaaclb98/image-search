"""
indexer/schema.py — canonical Qdrant point payload schema.

Single source of truth for every field name and type stored on a
Qdrant point. Both `indexer.upsert.build_payload` (writer) and
`search.*` (readers) import from here so a typo in one place can't
silently break the search side. The schema doc that lives at
`SCHEMA.md` is generated from this file by hand (kept in sync; the
two are intentionally separate surfaces — code constants are
typed, the doc is prose).

Adding a field:
  1. Add the constant below.
  2. Extend `Payload` TypedDict with the matching key/type.
  3. Update `build_payload` to populate it.
  4. Update `SCHEMA.md`.
  5. If readers need it, add the constant import to the consumer.

Renaming or removing a field is a breaking change to the on-disk
collection. Coordinate a backfill / migration before shipping.
"""

from __future__ import annotations

from typing import TypedDict

# --- Top-level payload fields --------------------------------------------

FIELD_ID = "id"
FIELD_PATH = "path"
FIELD_SHARD = "shard"
FIELD_COLLECTION = "collection"
FIELD_BLURHASH = "blurhash"
FIELD_MTIME = "mtime"
FIELD_SIZE = "size"
FIELD_MODEL_NAME = "model_name"
FIELD_MODEL_REVISION = "model_revision"
FIELD_INDEXED_AT = "indexed_at"


# --- Diversity fingerprints (sub-keys; same flat payload) ---------------

FIELD_CONTENT_SHA256 = "content_sha256"
FIELD_DHASH = "dhash"

# All fingerprint keys in one place so callers can iterate without
# listing them by hand.
FINGERPRINT_FIELDS: tuple[str, ...] = (FIELD_CONTENT_SHA256, FIELD_DHASH)


# --- TypedDict describing the full point payload ------------------------

class Payload(TypedDict, total=False):
    """Canonical Qdrant point payload (Qdrant stores payload as a
    free-form JSON object; TypedDict is for static typing only).

    `total=False` because every field except `id` / `path` can be
    missing on legacy points written before that field was added
    (the indexer tolerates missing values and the search side
    treats them as "unknown").
    """

    # Identity — always present on a freshly-built point.
    id: str
    path: str
    # Provenance.
    shard: str
    collection: str
    # LQIP placeholder; None when compute failed.
    blurhash: str | None
    # Diversity fingerprints; None when compute failed.
    content_sha256: str | None
    dhash: str | None
    # Filesystem metadata.
    mtime: int
    size: int
    # Embedding model provenance.
    model_name: str
    model_revision: str
    # ISO-8601 UTC timestamp of when the indexer wrote this point.
    indexed_at: str


def payload_field_names() -> tuple[str, ...]:
    """Tuple of every payload field name. Useful for tests and
    for generating the SCHEMA.md field table by hand."""
    return (
        FIELD_ID,
        FIELD_PATH,
        FIELD_SHARD,
        FIELD_COLLECTION,
        FIELD_BLURHASH,
        *FINGERPRINT_FIELDS,
        FIELD_MTIME,
        FIELD_SIZE,
        FIELD_MODEL_NAME,
        FIELD_MODEL_REVISION,
        FIELD_INDEXED_AT,
    )
