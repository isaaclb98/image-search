"""
search/models.py — Pydantic response models for /api/search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    id: str = Field(..., description="Qdrant point id (32-char hex prefix)")
    path: str = Field(..., description="Absolute source path on the NAS")
    score: float = Field(..., description="Cosine similarity in [-1, 1]")
    score_str: str = Field(
        "", description="Score formatted to 3 decimals, e.g. '0.873'. "
                       "Computed server-side so SSR + JS render identically."
    )
    url: str = Field("", description="Public URL for the /photo/{id}/raw endpoint")
    is_favorite: bool = False
    is_disliked: bool = False
    blurhash: str | None = None
    width: int | None = None
    height: int | None = None


class DiversityMetadata(BaseModel):
    """What the search-side Diversity ranker actually did."""

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


class SearchResponse(BaseModel):
    query: str
    positives: list[str] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)
    diverse: bool = False
    diversity: DiversityMetadata = Field(
        default_factory=DiversityMetadata,
        description="Diagnostics for the search-only Diversity ranking pass.",
    )
    surprise: bool = False
    view: str = "grid"
    centroid: str | None = None
    centroids: list[str] = Field(
        default_factory=list,
        description=(
            "Active centroid names in blend order. Empty when no centroid "
            "search is in play. One or more names blends via weighted mean."
        ),
    )
    weights: list[float] | None = None
    results: list[SearchResult]
    took_ms: int
    offset: int = 0
    limit: int = Field(..., description="Max results requested for this page")
    has_more: bool = False
    session_id: str | None = None
    session_total: int | None = None


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    code: str  # "bad_request" | "qdrant_unreachable" | "qdrant_timeout" | "internal_error" | "not_found" | "conflict"


# ---------------- Favourites ----------------


class FavoriteToggleResponse(BaseModel):
    id: str
    favorited_at: str


class FavoriteItem(BaseModel):
    id: str
    path: str
    favorited_at: str


class FavoritesListResponse(BaseModel):
    favorites: list[FavoriteItem]
    total: int
    limit: int
    offset: int


# ---------------------- Albums ----------------------
#
# User-curated collections of favourites. The favourites table is
# the implicit default album — there's no row for it in the
# `albums` table, so every API here is for user-created albums
# only. Membership is independent of favourites status: a photo
# can be in an album without being favourited, and vice versa.

class AlbumSummary(BaseModel):
    """Lightweight album row for list views.

    `member_count` is the count from `album_memberships`, which
    includes orphan memberships (favourites whose photo is no
    longer in the cache). For a UI count that hides orphans, use
    the detail endpoint.

    `first_member_id` is the chronologically first photo added to
    the album (ORDER BY album_memberships.added_at ASC LIMIT 1).
    Drives the /albums index card thumbnail — prefer it over
    `cover_favorite_id` for display. Empty string when the album
    has no members yet.
    """
    id: int
    name: str
    description: str
    cover_favorite_id: str
    first_member_id: str = ""
    member_count: int
    created_at: str
    updated_at: str


class AlbumsListResponse(BaseModel):
    albums: list[AlbumSummary]


class AlbumCreateRequest(BaseModel):
    name: str
    description: str = ""


class AlbumUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class AlbumMemberItem(BaseModel):
    id: str
    path: str
    added_at: str


class AlbumDetailResponse(BaseModel):
    """Full album row + paginated members (UI rendering shape).

    Members are photo metadata joined from the `images` cache, so
    orphan memberships (favourites whose photo is gone) are hidden.
    The album centroid compute still sees them via
    `list_album_member_ids`.
    """
    id: int
    name: str
    description: str
    cover_favorite_id: str
    created_at: str
    updated_at: str
    members: list[AlbumMemberItem]
    member_total: int  # excludes orphans; matches list_album_members count


class AlbumMemberResponse(BaseModel):
    album_id: int
    favorite_id: str
    added_at: str


class AlbumMembershipsResponse(BaseModel):
    """List of albums containing a given favourite, used by the
    per-photo UI to show which albums a photo is in."""
    favorite_id: str
    albums: list[AlbumSummary]


# ---------------- Saved searches ----------------
#
# Named prompt presets. The user saves a (positives, negatives) combo
# under a human-readable name ("red-dress-no-manikin") and can re-apply
# it later from the search bar dropdown. Only the prompt text is stored
# — view, centroid, favourites-filter and result limits are session
# state and intentionally NOT part of the saved shape.


class SavedSearchCreateRequest(BaseModel):
    """Body for POST /api/saved-searches.

    `positives` / `negatives` are lists of free-text prompts. Both
    may be empty, but at least one prompt total must be present
    (the route enforces this with a 400 if neither list has a
    non-empty entry). Name is trimmed and length-checked in the
    route (1–80 chars after strip).
    """
    name: str
    positives: list[str] = []
    negatives: list[str] = []


class SavedSearch(BaseModel):
    """One saved-search row, as returned by every saved-search
    endpoint. `positives` / `negatives` are always Python lists of
    strings on the wire — the IndexDB serialises JSON on disk and
    deserialises on read so callers don't need to think about the
    on-disk shape.
    """
    id: int
    name: str
    positives: list[str]
    negatives: list[str]
    created_at: str


class SavedSearchListResponse(BaseModel):
    """Paginated list response for GET /api/saved-searches.

    Newest-first ordering matches the dropdown UX (most recently
    saved at the top of the list). `total` is the unpaginated row
    count so the UI can show a "showing N of M" hint if it ever
    wants to.
    """
    saved_searches: list[SavedSearch]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class PromptState:
    """Normalized q/positive/negative prompt state for search.

    Plain dataclass (not a Pydantic BaseModel) because it's an
    internal value object — it's only ever passed around between
    the prompt-normalisation helper and the search route, never
    serialised to JSON.
    """
    q: str
    positives: list[str]
    negatives: list[str]
    positive_chips: list[str]
    negative_chips: list[str]


# --- Admin Index API ----------------------------------------------------
# Surfaces the in-process indexer runner over HTTP. The runner is owned
# by the search app and exposes its state via `to_dict()`. The router
# translates those snapshots to Pydantic models for typed OpenAPI.


class IndexerRunRequest(BaseModel):
    """Body for POST /api/admin/index.

    `mode` is the only user-visible knob: `incremental` (default)
    embeds only new/changed files; `rebuild` wipes the Qdrant
    collection + SQLite side store first.
    """
    mode: Literal["incremental", "rebuild"] = "incremental"


class IndexerProgressModel(BaseModel):
    indexed: int
    reembedded: int
    skipped: int
    errors: int


class IndexerStatusResponse(BaseModel):
    """Body for GET /api/admin/index/status."""
    state: Literal["idle", "running", "failed"]
    mode: Literal["incremental", "rebuild"] | None
    job_id: str | None
    pid: int | None
    started_at: str | None
    finished_at: str | None
    last_run_at: str | None
    last_error: str | None
    progress: IndexerProgressModel
    points_count: int | None
    # Sub-phase within `running`: "scanning" while walking the
    # filesystem, "warming_up" while the encoder is being downloaded
    # / loaded on first batch, "embedding" once we're actively
    # upserting. None outside of `running`.
    phase: Literal["scanning", "warming_up", "embedding"] | None = None


class IndexerLogResponse(BaseModel):
    """Body for GET /api/admin/index/log."""
    lines: list[str]
    next_line: int
    total: int

