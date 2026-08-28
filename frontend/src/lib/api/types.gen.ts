export interface paths {
    "/albums/{album_id}/download.zip": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Album Download Zip
         * @description Stream an album's photos as a zip — same shape and rules
         *     as `/favorites/download.zip`. Membership comes from
         *     `index_db.list_album_members` (INNER JOIN against the cache,
         *     so orphan rows are hidden from the archive too). Files we
         *     can't resolve on disk are skipped and recorded in
         *     `_missing.txt`. Album id with no row → 404.
         */
        get: operations["album_download_zip_albums__album_id__download_zip_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        /**
         * Album Download Zip
         * @description Stream an album's photos as a zip — same shape and rules
         *     as `/favorites/download.zip`. Membership comes from
         *     `index_db.list_album_members` (INNER JOIN against the cache,
         *     so orphan rows are hidden from the archive too). Files we
         *     can't resolve on disk are skipped and recorded in
         *     `_missing.txt`. Album id with no row → 404.
         */
        head: operations["album_download_zip_albums__album_id__download_zip_get_1"];
        patch?: never;
        trace?: never;
    };
    "/api/albums": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Albums */
        get: operations["list_albums_api_albums_get"];
        put?: never;
        /** Create Album */
        post: operations["create_album_api_albums_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/albums/by-favorite/{favorite_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Albums For Favorite
         * @description Return every album that contains `favorite_id`.
         *
         *     Used by the per-photo UI to show which albums a photo is
         *     in. The summary shape omits member_count (always 1 for
         *     this view) so we re-use AlbumSummary with count=1.
         */
        get: operations["list_albums_for_favorite_api_albums_by_favorite__favorite_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/albums/{album_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Album */
        get: operations["get_album_api_albums__album_id__get"];
        put?: never;
        post?: never;
        /** Delete Album */
        delete: operations["delete_album_api_albums__album_id__delete"];
        options?: never;
        head?: never;
        /** Update Album */
        patch: operations["update_album_api_albums__album_id__patch"];
        trace?: never;
    };
    "/api/albums/{album_id}/members/{favorite_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Add Album Member */
        post: operations["add_album_member_api_albums__album_id__members__favorite_id__post"];
        /** Remove Album Member */
        delete: operations["remove_album_member_api_albums__album_id__members__favorite_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cache/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Api Cache Refresh */
        get: operations["api_cache_refresh_api_cache_refresh_post"];
        put?: never;
        /** Api Cache Refresh */
        post: operations["api_cache_refresh_api_cache_refresh_post_1"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/cache/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Cache Status
         * @description Operator visibility into the dual-store sync.
         *
         *     Returns last refresh timestamp + duration, point counts in
         *     both stores, drift between them, the liveness cache size +
         *     cap, and the configured refresh interval / TTL. Drift is
         *     "unknown" when Qdrant is unreachable (qdrant_count == -1)
         *     so operators don't see a misleading negative number.
         */
        get: operations["cache_status_api_cache_status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/centroids": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Centroids
         * @description List static + dynamic centroids with metadata.
         */
        get: operations["list_centroids_api_centroids_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/centroids/reload": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reload Centroids */
        post: operations["reload_centroids_api_centroids_reload_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/centroids/{name}/search": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Search By Centroid
         * @description Search using a loaded centroid as the query vector.
         */
        get: operations["search_by_centroid_api_centroids__name__search_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/collections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Collections
         * @description List distinct library collections with point counts.
         */
        get: operations["list_collections_api_collections_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/dislikes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Dislikes */
        get: operations["list_dislikes_api_dislikes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/dislikes/{point_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Mark Dislike */
        post: operations["mark_dislike_api_dislikes__point_id__post"];
        /** Unmark Dislike */
        delete: operations["unmark_dislike_api_dislikes__point_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/favorites": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Api Favorites */
        get: operations["api_favorites_api_favorites_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/favorites/{point_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Mark Favorite */
        post: operations["mark_favorite_api_favorites__point_id__post"];
        /** Unmark Favorite */
        delete: operations["unmark_favorite_api_favorites__point_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/for-you/diversity": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * For You Diversity
         * @description Expose the active Diversity defaults + valid choices to the UI.
         */
        get: operations["for_you_diversity_api_for_you_diversity_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/for-you/feed": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * For You Feed
         * @description Paginated, server-side for-you feed.
         *
         *     Diversity is resolved against the app‑wide `cfg.diversity`
         *     default; `diversity_depth` is accepted for API parity but
         *     ignored (only the discovery rabbithole uses depth today).
         *
         *     `limit` is clamped to [1, 100] silently inside the handler
         *     so callers can ask for `limit=999` and get the largest valid
         *     page rather than a 422.
         */
        get: operations["for_you_feed_api_for_you_feed_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/for-you/reset": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * For You Reset
         * @description Invalidate the cached user signal + favourites centroid.
         */
        post: operations["for_you_reset_api_for_you_reset_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/for-you/state": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * For You State
         * @description Cheap signal snapshot for the header chip and empty-state.
         */
        get: operations["for_you_state_api_for_you_state_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/photo/{point_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Photo Metadata
         * @description Fetch metadata for a single photo by ID.
         *
         *     Used by the frontend's dedicated photo page to render the
         *     large photo + metadata sidebar. Returns everything the
         *     sidebar can show: identity, file info, indexing info,
         *     favourite status. Does NOT return the vector — that's a
         *     different concern, and `payload` carries enough for the UI.
         */
        get: operations["photo_metadata_api_photo__point_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/random": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Api Random */
        get: operations["api_random_api_random_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/saved-searches": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Saved Searches */
        get: operations["list_saved_searches_api_saved_searches_get"];
        put?: never;
        /** Create Saved Search */
        post: operations["create_saved_search_api_saved_searches_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/saved-searches/{saved_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Saved Search */
        get: operations["get_saved_search_api_saved_searches__saved_id__get"];
        put?: never;
        post?: never;
        /** Delete Saved Search */
        delete: operations["delete_saved_search_api_saved_searches__saved_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/search": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Api Search */
        get: operations["api_search_api_search_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/similar/{point_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Similar Photos
         * @description Most-similar photos: nearest neighbours of `point_id`.
         */
        get: operations["similar_photos_api_similar__point_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sync/pause": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sync Pause */
        post: operations["sync_pause_api_sync_pause_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sync/resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sync Resume */
        post: operations["sync_resume_api_sync_resume_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sync/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Sync Status */
        get: operations["sync_status_api_sync_status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/system/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * System Status
         * @description System status with cache stats for the frontend dashboard.
         */
        get: operations["system_status_api_system_status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/favorites/download.zip": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Favorites Download Zip */
        get: operations["favorites_download_zip_favorites_download_zip_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        /** Favorites Download Zip */
        head: operations["favorites_download_zip_favorites_download_zip_get_1"];
        patch?: never;
        trace?: never;
    };
    "/healthz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Healthz */
        get: operations["healthz_healthz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/photo/{point_id}/raw": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Photo Raw */
        get: operations["photo_raw_photo__point_id__raw_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/thumb/{point_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Thumbnail
         * @description Serve a pre-generated WebP thumbnail.
         *
         *     Args:
         *         point_id: Qdrant point ID (32-char hex)
         *
         *     Returns:
         *         WebP file with immutable cache headers
         *
         *     Raises:
         *         404 if thumbnail doesn't exist (frontend falls back to blurhash)
         */
        get: operations["get_thumbnail_thumb__point_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AlbumCreateRequest */
        AlbumCreateRequest: {
            /**
             * Description
             * @default
             */
            description: string;
            /** Name */
            name: string;
        };
        /**
         * AlbumDetailResponse
         * @description Full album row + paginated members (UI rendering shape).
         *
         *     Members are photo metadata joined from the `images` cache, so
         *     orphan memberships (favourites whose photo is gone) are hidden.
         *     The album centroid compute still sees them via
         *     `list_album_member_ids`.
         */
        AlbumDetailResponse: {
            /** Cover Favorite Id */
            cover_favorite_id: string;
            /** Created At */
            created_at: string;
            /** Description */
            description: string;
            /** Id */
            id: number;
            /** Member Total */
            member_total: number;
            /** Members */
            members: components["schemas"]["AlbumMemberItem"][];
            /** Name */
            name: string;
            /** Updated At */
            updated_at: string;
        };
        /** AlbumMemberItem */
        AlbumMemberItem: {
            /** Added At */
            added_at: string;
            /** Id */
            id: string;
            /** Path */
            path: string;
        };
        /** AlbumMemberResponse */
        AlbumMemberResponse: {
            /** Added At */
            added_at: string;
            /** Album Id */
            album_id: number;
            /** Favorite Id */
            favorite_id: string;
        };
        /**
         * AlbumMembershipsResponse
         * @description List of albums containing a given favourite, used by the
         *     per-photo UI to show which albums a photo is in.
         */
        AlbumMembershipsResponse: {
            /** Albums */
            albums: components["schemas"]["AlbumSummary"][];
            /** Favorite Id */
            favorite_id: string;
        };
        /**
         * AlbumSummary
         * @description Lightweight album row for list views.
         *
         *     `member_count` is the count from `album_memberships`, which
         *     includes orphan memberships (favourites whose photo is no
         *     longer in the cache). For a UI count that hides orphans, use
         *     the detail endpoint.
         *
         *     `first_member_id` is the chronologically first photo added to
         *     the album (ORDER BY album_memberships.added_at ASC LIMIT 1).
         *     Drives the /albums index card thumbnail — prefer it over
         *     `cover_favorite_id` for display. Empty string when the album
         *     has no members yet.
         */
        AlbumSummary: {
            /** Cover Favorite Id */
            cover_favorite_id: string;
            /** Created At */
            created_at: string;
            /** Description */
            description: string;
            /**
             * First Member Id
             * @default
             */
            first_member_id: string;
            /** Id */
            id: number;
            /** Member Count */
            member_count: number;
            /** Name */
            name: string;
            /** Updated At */
            updated_at: string;
        };
        /** AlbumUpdateRequest */
        AlbumUpdateRequest: {
            /** Description */
            description?: string | null;
            /** Name */
            name?: string | null;
        };
        /** AlbumsListResponse */
        AlbumsListResponse: {
            /** Albums */
            albums: components["schemas"]["AlbumSummary"][];
        };
        /**
         * DiversityMetadata
         * @description What the search-side Diversity ranker actually did.
         */
        DiversityMetadata: {
            /**
             * Applied
             * @default false
             */
            applied: boolean;
            /**
             * Candidate Count
             * @default 0
             */
            candidate_count: number;
            /**
             * Depth
             * @description Requested candidate-pool depth: auto, 500, 1000, 2000, or 5000.
             * @default auto
             */
            depth: string;
            /**
             * Duplicate Images Collapsed
             * @default 0
             */
            duplicate_images_collapsed: number;
            /**
             * Mode
             * @default off
             */
            mode: string;
            /**
             * Pool Depth
             * @description Number of candidates actually retrieved for the ranking pass.
             * @default 0
             */
            pool_depth: number;
            /**
             * Requested
             * @default false
             */
            requested: boolean;
            /**
             * Result Count
             * @default 0
             */
            result_count: number;
            /**
             * Semantic Groups Covered
             * @default 0
             */
            semantic_groups_covered: number;
            /**
             * Strength
             * @default 0
             */
            strength: number;
        };
        /** FavoriteToggleResponse */
        FavoriteToggleResponse: {
            /** Favorited At */
            favorited_at: string;
            /** Id */
            id: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * SavedSearch
         * @description One saved-search row, as returned by every saved-search
         *     endpoint. `positives` / `negatives` are always Python lists of
         *     strings on the wire — the IndexDB serialises JSON on disk and
         *     deserialises on read so callers don't need to think about the
         *     on-disk shape.
         */
        SavedSearch: {
            /** Created At */
            created_at: string;
            /** Id */
            id: number;
            /** Name */
            name: string;
            /** Negatives */
            negatives: string[];
            /** Positives */
            positives: string[];
        };
        /**
         * SavedSearchCreateRequest
         * @description Body for POST /api/saved-searches.
         *
         *     `positives` / `negatives` are lists of free-text prompts. Both
         *     may be empty, but at least one prompt total must be present
         *     (the route enforces this with a 400 if neither list has a
         *     non-empty entry). Name is trimmed and length-checked in the
         *     route (1–80 chars after strip).
         */
        SavedSearchCreateRequest: {
            /** Name */
            name: string;
            /**
             * Negatives
             * @default []
             */
            negatives: string[];
            /**
             * Positives
             * @default []
             */
            positives: string[];
        };
        /**
         * SavedSearchListResponse
         * @description Paginated list response for GET /api/saved-searches.
         *
         *     Newest-first ordering matches the dropdown UX (most recently
         *     saved at the top of the list). `total` is the unpaginated row
         *     count so the UI can show a "showing N of M" hint if it ever
         *     wants to.
         */
        SavedSearchListResponse: {
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Saved Searches */
            saved_searches: components["schemas"]["SavedSearch"][];
            /** Total */
            total: number;
        };
        /** SearchResponse */
        SearchResponse: {
            /**
             * Centroid
             * @description First active centroid name, when any centroids are in play. Kept for backward compat with single-centroid clients; the full list lives in `centroids`. Mutually exclusive with q/positives/negatives.
             */
            centroid?: string | null;
            /**
             * Centroids
             * @description Active centroid names in blend order. Empty when no centroid search is in play. One or more names blends via weighted mean.
             */
            centroids?: string[];
            /**
             * Diverse
             * @description Backwards-compatible flag; true when search Diversity was applied.
             * @default false
             */
            diverse: boolean;
            /** @description Diagnostics for the search-only Diversity ranking pass. */
            diversity?: components["schemas"]["DiversityMetadata"];
            /**
             * Has More
             * @description True when more results likely exist on a subsequent page
             * @default false
             */
            has_more: boolean;
            /**
             * Limit
             * @description Max results requested for this page
             */
            limit: number;
            /** Negatives */
            negatives?: string[];
            /**
             * Offset
             * @description Offset of this page in the full result set
             * @default 0
             */
            offset: number;
            /** Positives */
            positives?: string[];
            /** Query */
            query: string;
            /** Results */
            results: components["schemas"]["SearchResult"][];
            /**
             * Session Id
             * @description Opaque session id. For /api/random this identifies the shuffled deck; pass it back with an incremented `offset` to walk forward. None for non-session endpoints (search, similar, etc.) — those use offset/limit directly against Qdrant.
             */
            session_id?: string | null;
            /**
             * Session Total
             * @description Total photos in the session deck. Only set for /api/random. Use this with `offset` to know when you've walked everything.
             */
            session_total?: number | null;
            /**
             * Surprise
             * @description True when results were randomly sampled from a deep pool (Surprise Me mode).
             * @default false
             */
            surprise: boolean;
            /** Took Ms */
            took_ms: number;
            /**
             * View
             * @description Result view requested: 'grid' (default) or 'feed' (single-column, full-width).
             * @default grid
             */
            view: string;
            /**
             * Weights
             * @description Per-centroid weights, same order as `centroids`. None means all weights equal 1.0 (the default).
             */
            weights?: number[] | null;
        };
        /** SearchResult */
        SearchResult: {
            /**
             * Blurhash
             * @description LQIP (low-quality image placeholder). Decoded client-side into a tinted background while the real image loads. None when the encoder failed or the point was indexed before the blurhash feature shipped.
             */
            blurhash?: string | null;
            /**
             * Height
             * @description Photo height in pixels, when known. None for older rows that were indexed before width/height were recorded.
             */
            height?: number | null;
            /**
             * Id
             * @description Qdrant point id (32-char hex prefix)
             */
            id: string;
            /**
             * Is Disliked
             * @description True when the image is marked as a dislike (hides it from future recommendations)
             * @default false
             */
            is_disliked: boolean;
            /**
             * Is Favorite
             * @description True when the image is marked as a favourite
             * @default false
             */
            is_favorite: boolean;
            /**
             * Path
             * @description Absolute source path on the NAS
             */
            path: string;
            /**
             * Score
             * @description Cosine similarity in [-1, 1]
             */
            score: number;
            /**
             * Score Str
             * @description Score formatted to 3 decimals, e.g. '0.873'. Computed server-side so SSR + JS render identically.
             * @default
             */
            score_str: string;
            /**
             * Url
             * @description Public URL for the /photo/{id}/raw endpoint
             * @default
             */
            url: string;
            /**
             * Width
             * @description Photo width in pixels, when known. None for older rows that were indexed before width/height were recorded.
             */
            width?: number | null;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    album_download_zip_albums__album_id__download_zip_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                album_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    album_download_zip_albums__album_id__download_zip_get_1: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                album_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_albums_api_albums_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AlbumsListResponse"];
                };
            };
        };
    };
    create_album_api_albums_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AlbumCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AlbumSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_albums_for_favorite_api_albums_by_favorite__favorite_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                favorite_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AlbumMembershipsResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_album_api_albums__album_id__get: {
        parameters: {
            query?: {
                /** @description max members to return */
                limit?: number;
                /** @description offset into members */
                offset?: number;
            };
            header?: never;
            path: {
                album_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AlbumDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_album_api_albums__album_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                album_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_album_api_albums__album_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                album_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AlbumUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AlbumSummary"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    add_album_member_api_albums__album_id__members__favorite_id__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                album_id: number;
                favorite_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AlbumMemberResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    remove_album_member_api_albums__album_id__members__favorite_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                album_id: number;
                favorite_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    api_cache_refresh_api_cache_refresh_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    api_cache_refresh_api_cache_refresh_post_1: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    cache_status_api_cache_status_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    list_centroids_api_centroids_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    reload_centroids_api_centroids_reload_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    search_by_centroid_api_centroids__name__search_get: {
        parameters: {
            query?: {
                /** @description max results */
                limit?: number;
                /** @description offset into the full result set */
                offset?: number;
            };
            header?: never;
            path: {
                name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_collections_api_collections_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    list_dislikes_api_dislikes_get: {
        parameters: {
            query?: {
                /** @description max dislikes */
                limit?: number;
                /** @description offset into dislikes */
                offset?: number;
                /** @description return SearchResponse-compatible shape */
                as_results?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    mark_dislike_api_dislikes__point_id__post: {
        parameters: {
            query?: {
                source?: string;
            };
            header?: never;
            path: {
                point_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    unmark_dislike_api_dislikes__point_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                point_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    api_favorites_api_favorites_get: {
        parameters: {
            query?: {
                /** @description max favourites */
                limit?: number;
                /** @description offset into favourites */
                offset?: number;
                /** @description return SearchResponse-compatible shape */
                as_results?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    mark_favorite_api_favorites__point_id__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                point_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FavoriteToggleResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    unmark_favorite_api_favorites__point_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                point_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    for_you_diversity_api_for_you_diversity_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    for_you_feed_api_for_you_feed_get: {
        parameters: {
            query?: {
                /** @description max recommendations per page */
                limit?: number;
                /** @description zero-based page index */
                page?: number;
                /** @description diversity mode */
                diversity?: string | null;
                /** @description ignored on /for-you */
                diversity_depth?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    for_you_reset_api_for_you_reset_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    for_you_state_api_for_you_state_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    photo_metadata_api_photo__point_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                point_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    api_random_api_random_get: {
        parameters: {
            query?: {
                /** @description max results */
                limit?: number;
                /** @description position in the shuffled deck */
                offset?: number;
                /** @description session id from a previous /api/random response */
                session?: string | null;
                /** @description restrict to one or more collections; empty = whole set */
                collections?: string[] | null;
                /** @description result view: 'grid' or 'feed' */
                view?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_saved_searches_api_saved_searches_get: {
        parameters: {
            query?: {
                /** @description max saved searches */
                limit?: number;
                /** @description offset into saved searches */
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SavedSearchListResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_saved_search_api_saved_searches_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SavedSearchCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SavedSearch"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_saved_search_api_saved_searches__saved_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                saved_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SavedSearch"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_saved_search_api_saved_searches__saved_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                saved_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    api_search_api_search_get: {
        parameters: {
            query?: {
                /** @description text query */
                q?: string;
                /** @description max results */
                limit?: number;
                /** @description offset into the full result set */
                offset?: number;
                /** @description result view: 'grid' or 'feed' */
                view?: string;
                /** @description restrict results to favourites */
                favorites?: boolean;
                /** @description apply MMR diversity re-ranking */
                diverse?: boolean;
                /** @description Diversity strength: off, low, balanced, or high */
                diversity?: string | null;
                /** @description Diversity candidate depth: auto, 500, 1000, 2000, or 5000 */
                diversity_depth?: string | null;
                /** @description Surprise Me — random sample from deep pool */
                surprise?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    similar_photos_api_similar__point_id__get: {
        parameters: {
            query?: {
                /** @description max similar photos to return */
                limit?: number;
                /** @description offset into the result set */
                offset?: number;
            };
            header?: never;
            path: {
                point_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    sync_pause_api_sync_pause_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    sync_resume_api_sync_resume_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    sync_status_api_sync_status_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    system_status_api_system_status_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    favorites_download_zip_favorites_download_zip_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    favorites_download_zip_favorites_download_zip_get_1: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    healthz_healthz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    photo_raw_photo__point_id__raw_get: {
        parameters: {
            query?: {
                /** @description Optional target width in pixels. Server Lanczos-resizes if set. */
                w?: number | null;
            };
            header?: never;
            path: {
                point_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_thumbnail_thumb__point_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                point_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
