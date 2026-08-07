"""
search/models.py — Pydantic response models for /api/search.
"""

from __future__ import annotations

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
    is_favorite: bool = Field(False, description="True when the image is marked as a favourite")
    blurhash: str | None = Field(
        None,
        description=(
            "LQIP (low-quality image placeholder). Decoded client-side into a tinted"
            " background while the real image loads. None when the encoder failed"
            " or the point was indexed before the blurhash feature shipped."
        ),
    )


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


class SearchResponse(BaseModel):
    query: str
    positives: list[str] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)
    diverse: bool = Field(
        False,
        description="Backwards-compatible flag; true when search Diversity was applied.",
    )
    diversity: DiversityMetadata = Field(
        default_factory=DiversityMetadata,
        description="Diagnostics for the search-only Diversity ranking pass.",
    )
    surprise: bool = Field(
        False,
        description="True when results were randomly sampled from a deep pool (Surprise Me mode).",
    )
    view: str = Field(
        "grid",
        description="Result view requested: 'grid' (default) or 'feed' (single-column, full-width).",
    )
    centroid: str | None = Field(
        None,
        description=(
            "First active centroid name, when any centroids are in play. "
            "Kept for backward compat with single-centroid clients; the "
            "full list lives in `centroids`. Mutually exclusive with "
            "q/positives/negatives."
        ),
    )
    centroids: list[str] = Field(
        default_factory=list,
        description=(
            "Active centroid names in blend order. Empty when no centroid "
            "search is in play. One or more names blends via weighted mean."
        ),
    )
    weights: list[float] | None = Field(
        None,
        description=(
            "Per-centroid weights, same order as `centroids`. None means "
            "all weights equal 1.0 (the default)."
        ),
    )
    results: list[SearchResult]
    took_ms: int
    offset: int = Field(0, description="Offset of this page in the full result set")
    limit: int = Field(..., description="Max results requested for this page")
    has_more: bool = Field(
        False,
        description="True when more results likely exist on a subsequent page",
    )


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    code: str  # "bad_request" | "qdrant_unreachable" | "qdrant_timeout" | "internal_error" | "not_found" | "conflict"


# ---------------- Discovery rabbithole ----------------


class DiscoveryImage(BaseModel):
    """One image in a discovery feed pair or in the liked gallery."""
    id: str
    path: str
    url: str = Field("", description="Public URL for /photo/{id}/raw")
    blurhash: str | None = Field(
        None, description="Optional client-decoded low-quality image placeholder."
    )
    is_favorite: bool = Field(False, description="True when the image is in favourites.")
    # When present (gallery only), the round in which the user
    # picked this image. Pairs in the live feed don't set this.
    picked_round: int | None = None


class DiscoveryPair(BaseModel):
    """A two-image pair shown to the user. The user picks one."""
    round: int = Field(..., description="1-based round number for this pair")
    left: DiscoveryImage | None = None
    right: DiscoveryImage | None = None
    source: str = Field(
        "",
        description="Where the pair came from: 'random' (seed phase) or 'recommend'.",
    )


class DiscoveryStartResponse(BaseModel):
    session_id: str
    pair: DiscoveryPair


class DiscoveryPickResponse(BaseModel):
    pair: DiscoveryPair | None = Field(
        None,
        description="Next pair. None if the session is gone (treat as 'start over').",
    )
    round: int = Field(..., description="Round number the user just completed (1-based).")
    liked_count: int = Field(..., description="Total picks in this session so far.")


class DiscoveryLikedResponse(BaseModel):
    session_id: str
    images: list[DiscoveryImage]


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
