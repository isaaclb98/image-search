<script lang="ts">
  /**
   * Home page — single landing experience per the merge (round‑23).
   *
   * Combines what used to live on `/` and `/search`:
   *   - SearchComposer at the top
   *   - Inline search results (paginated via loadMore on scroll)
   *   - For‑You row at the bottom (sample of recommendations)
   *
   * URL ⇄ state sync:
   *   - On mount, read URL params into local state, fire a search.
   *   - When state changes, write back to the URL via replaceState
   *     so reloads and share‑links restore the same query.
   *   - Search runs ONLY on explicit Search button clicks (issue #6);
   *     typing or toggling a select updates the URL but does not
   *     hit /api/search.
   *
   * SearchComposer is a pure UI child — the page owns the truth.
   */
  import { page } from '$app/stores';
  import { onMount, tick } from 'svelte';
  import { browser } from '$app/environment';
  import SearchComposer from '$lib/components/SearchComposer.svelte';
  import SavedSearchesMenu from '$lib/components/SavedSearchesMenu.svelte';
  import AdditionalFilters from '$lib/components/AdditionalFilters.svelte';
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
  import {
    search,
    likePoint,
    unlikePoint,
    dislikePoint
  } from '$lib/api/endpoints';
  import type { SavedSearch } from '$lib/api/endpoints';
  import { GRID_PAGE_SIZE } from '$lib/api/limits';
  import { toast } from '$lib/components/Toaster.svelte';

  type Item = {
    id: string;
    path?: string;
    score?: number;
    score_str?: string;
    blurhash?: string | null;
    is_favorite?: boolean;
    is_disliked?: boolean;
  };

  const PAGE = GRID_PAGE_SIZE;

  // Round‑34: sample-centroid K. Mirrors the backend default
  // (`DEFAULT_SAMPLE_K` in `search/centroids_compute.py`).
  // Surface this constant in the home-page header so the
  // "Sample mode — based on a random N photos" copy stays
  // honest if the backend default ever changes.
  const SAMPLE_K = 10;

  // Composer state (hoisted from SearchComposer).
  let positives = $state<string[]>([]);
  let negatives = $state<string[]>([]);
  let input = $state('');
  let mode = $state<'pos' | 'neg'>('pos');
  let filename = $state('');
  let diversityMode = $state('off');
  let diversityDepth = $state('auto');
  let collections = $state<string[]>([]);
  let filtersOpen = $state(false);

  // Results state.
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(false);
  let offset = $state(0);
  let error = $state<string | null>(null);
  let ctrl: AbortController | null = null;
  let hasSearched = $state(false);
  // Round-31: show a one-time empty-index prompt when the user has
  // never indexed anything. Detected on mount via /api/admin/index/status;
  // disappears once any job has run successfully (last_run_at != null).
  let indexIsEmpty = $state(false);
  // Round‑29: when set, the page treats this as a "search by album
  // centroid" — the SearchComposer is hidden and reload() hits the
  // centroid endpoint instead of /api/search. URL ?centroid=...
  // sets this on mount; writing back to URL is suppressed.
  let activeCentroid = $state<string | null>(null);

  // Round‑34: sample-centroid mode flag. Only meaningful when
  // `activeCentroid` is also set — the home page reads `?mode=`
  // alongside `?centroid=` and forwards it to /api/centroids/...
  // /search. The "Surprise me" button on /albums writes both
  // params; the album-detail page or other callers don't
  // currently set this.
  let centroidMode = $state<'centroid' | 'sample'>('centroid');

  function readFromUrl() {
    const q = $page.url.searchParams;
    positives = q.getAll('positives');
    negatives = q.getAll('negatives');
    filename = q.get('filename') ?? '';
    diversityMode = q.get('diversity') ?? 'off';
    diversityDepth = q.get('diversity_depth') ?? 'auto';
    collections = q.getAll('collections');
    filtersOpen = !!filename || diversityMode !== 'off' || diversityDepth !== 'auto' || collections.length > 0;
    activeCentroid = q.get('centroid');
    // Validate the mode param — anything other than the two
    // known values is treated as the default so a stale or
    // hand-edited URL doesn't 400 the page. The backend
    // validates again, but tolerating bad input here keeps the
    // UX forgiving.
    const rawMode = q.get('mode');
    centroidMode =
      rawMode === 'sample' || rawMode === 'centroid' ? rawMode : 'centroid';
  }

  function writeToUrl() {
    if (!browser) return;
    // Round‑29: don't clobber the ?centroid= param while we're
    // running a centroid search — that would cause the URL to
    // flicker and could re-trigger reload via onMount.
    if (activeCentroid) return;
    const qs = new URLSearchParams();
    positives.forEach((p) => qs.append('positives', p));
    negatives.forEach((n) => qs.append('negatives', n));
    if (filename) qs.set('filename', filename);
    if (diversityMode !== 'off') qs.set('diversity', diversityMode);
    if (diversityDepth && diversityDepth !== 'auto') qs.set('diversity_depth', diversityDepth);
    collections.forEach((c) => qs.append('collections', c));
    const next = qs.toString();
    if ($page.url.search.replace(/^\?/, '') !== next) {
      history.replaceState(history.state, '', `/${next ? '?' + next : ''}`);
    }
  }

  function addPrompt(text: string, kind: 'pos' | 'neg') {
    if (kind === 'pos') {
      if (!positives.includes(text)) positives = [...positives, text];
    } else {
      if (!negatives.includes(text)) negatives = [...negatives, text];
    }
  }
  function removePositive(i: number) {
    positives = positives.filter((_, idx) => idx !== i);
  }
  function removeNegative(i: number) {
    negatives = negatives.filter((_, idx) => idx !== i);
  }
  function toggleCollection(name: string) {
    collections = collections.includes(name)
      ? collections.filter((c) => c !== name)
      : [...collections, name];
  }

  async function reload() {
    if (ctrl) ctrl.abort();
    ctrl = new AbortController();
    offset = 0;
    items = [];
    error = null;
    hasMore = false;

    // Round‑29: centroid search activates on `?centroid=` even if
    // the composer is empty. Otherwise the user must fill a prompt
    // before the search runs (which doesn't make sense — the
    // centroid IS the query).
    //
    // A collections-only filter is also a valid trigger: the user
    // wants to scroll one specific library without typing any
    // semantic prompt.
    const hasCollectionFilter = collections.length > 0;
    const active =
      activeCentroid ||
      positives.length ||
      negatives.length ||
      filename.trim() ||
      hasCollectionFilter;
    if (!active) {
      loading = false;
      hasSearched = false;
      return;
    }
    hasSearched = true;
    loading = true;
    try {
      const res = await search(
        {
          positives,
          negatives,
          filename,
          diversityMode,
          diversityDepth,
          limit: PAGE,
          offset: 0,
          centroid: activeCentroid ?? undefined,
          // Only forward `centroidMode` when actually in a
          // centroid search — the bare /api/search endpoint
          // doesn't accept `mode=` and would 400. The endpoint
          // also defaults to 'centroid' when omitted, so omitting
          // is the safe choice for non-centroid paths.
          centroidMode:
            activeCentroid && centroidMode === 'sample'
              ? 'sample'
              : 'centroid',
          collections: collections.length ? collections : undefined
        },
        ctrl.signal
      );
      items = (res?.results ?? []) as Item[];
      offset = items.length;
      hasMore = items.length >= PAGE && !!res?.has_more;
    } catch (e: unknown) {
      if ((e as { name?: string }).name === 'AbortError') return;
      error = (e as Error).message ?? 'search failed';
    } finally {
      loading = false;
    }
  }

  async function loadMore(signal?: AbortSignal) {
    if (loading || !hasMore) return;
    loading = true;
    try {
      const res = await search({
        positives, negatives, filename,
        diversityMode, diversityDepth,
        limit: PAGE, offset,
        centroid: activeCentroid ?? undefined,
        centroidMode:
          activeCentroid && centroidMode === 'sample' ? 'sample' : 'centroid',
        collections: collections.length ? collections : undefined,
        signal
      });
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
      offset += more.length;
      hasMore = more.length >= PAGE && !!res?.has_more;
    } catch (e) {
      if (signal?.aborted) return; // clean cancel from pre-fetch retrigger
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  async function onToggleFavorite(id: string) {
    const it = items.find((x) => x.id === id);
    const liked = it?.is_favorite ?? false;
    try {
      if (liked) await unlikePoint(id);
      else await likePoint(id);
      items = items.map((x) =>
        x.id === id ? { ...x, is_favorite: !liked } : x
      );
    } catch {
      toast.show('Failed to update like.', { kind: 'error' });
    }
  }

  async function onDislike(id: string) {
    try {
      await dislikePoint(id);
      items = items.map((x) =>
        x.id === id ? { ...x, is_disliked: true } : x
      );
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
  }

  // Run on Search button (from composer) and on initial mount if
  // URL has params (so deep links like /?positives=cat or
  // /?centroid=album_2 still load).
  onMount(async () => {
    readFromUrl();
    await tick();
    if (
      activeCentroid ||
      positives.length ||
      negatives.length ||
      filename.trim()
    ) {
      await reload();
    }
    // Round-31: surface a one-time prompt when the library is
    // empty so first-time users discover Settings → Index.
    // Fire-and-forget — never blocks the search.
    //
    // "Empty" = the status endpoint reports `points_count === 0`.
    // We deliberately DO NOT key off `last_run_at`: a fresh dev
    // container that mounted pre-existing data has photos in the
    // cache but has never run the indexer, and shouldn't nag.
    // We also don't show the prompt when points_count is null
    // (unknown): false-negatives on this banner are much less
    // annoying than false-positives, so we assume "not empty"
    // when we can't tell.
    try {
      const s = await fetch('/api/admin/index/status', {
        credentials: 'include'
      });
      if (s.ok) {
        const body = await s.json();
        if (typeof body.points_count === 'number' && body.points_count === 0) {
          indexIsEmpty = true;
        }
      }
    } catch {
      // ignore — admin endpoint may be unavailable in tests
    }
  });

  // Mirror composer state into the URL (no auto‑reload; only
  // Search button clicks or picking a saved search fire reload).
  $effect(() => {
    void positives;
    void negatives;
    void filename;
    void diversityMode;
    void diversityDepth;
    void input;
    if (!browser) return;
    writeToUrl();
  });
</script>

<svelte:head>
  <title>Image Search</title>
</svelte:head>

<section class="hero">
  {#if indexIsEmpty}
    <!-- Round-31: empty-library discoverability. Shows once on a
         fresh install where no job has ever run. The user dismisses
         it by going to Settings → Index (or by closing it manually). -->
    <div class="empty-prompt" role="status">
      <span>No photos indexed yet.</span>
      <a href="/settings">Go to Settings → Index</a>
      <button class="dismiss" onclick={() => (indexIsEmpty = false)} aria-label="Dismiss">×</button>
    </div>
  {/if}
  {#if activeCentroid}
    <!-- Round‑29: search-by-album mode hides the prompt composer.
         The album's centroid IS the query; there's nothing to type.
         Round‑34: when in sample mode (from /albums "Surprise me"
         button), surface the active mode so the user knows why
         results are different from the deterministic centroid. -->
    <h1>Searching by album</h1>
    <p class="sub">
      {#if centroidMode === 'sample'}
        Sample mode — based on a random {SAMPLE_K} photos from <code>{activeCentroid}</code>.
        Refresh to re-roll, or
        <a href="/?centroid={encodeURIComponent(activeCentroid ?? '')}" class="back-link">
          switch back to the full mean
        </a>.
      {:else}
        Showing the photos closest to the average of <code>{activeCentroid}</code>.
        <a href="/?centroid={encodeURIComponent(activeCentroid ?? '')}&mode=sample" class="surprise-link">
          Surprise me
        </a> · <a href="/albums" class="back-link">← Back to albums</a>
      {/if}
    </p>
  {:else}
    <h1>Find photos by what they look like.</h1>
    <p class="sub">
      Type what you remember. Save what you love.
    </p>
  {/if}
  {#if !activeCentroid}
    <SearchComposer
      {positives}
      {negatives}
      {input}
      {mode}
      onInput={(v) => (input = v)}
      onMode={(m) => (mode = m)}
      onAdd={addPrompt}
      onRemovePositive={removePositive}
      onRemoveNegative={removeNegative}
    />
  {/if}

  <!-- Diversity / filename controls + collections chip filter.
       Rendered inside .hero (next to the SearchComposer, not nested
       inside the composer component) so they share the composer's
       card width — the user wants this panel to read as part of
       the search section, not the photo grid. -->
  <AdditionalFilters
    open={filtersOpen}
    {filename}
    {diversityMode}
    {diversityDepth}
    {collections}
    onToggle={() => (filtersOpen = !filtersOpen)}
    onFilename={(v) => (filename = v)}
    onDiversityMode={(v) => (diversityMode = v)}
    onDiversityDepth={(v) => (diversityDepth = v)}
    onToggleCollection={toggleCollection}
  />

  <!-- Search button + saved-searches menu. Pulled out of
       SearchComposer so it sits AFTER the additional-options
       panel — visual order: search inputs → diversity options
       → action buttons. -->
  {#if !activeCentroid}
    <div class="search-actions">
      <SavedSearchesMenu
        {positives}
        {negatives}
        onPick={(s: SavedSearch) => {
          // Populate the state but don't auto-run. The Search
          // button is now enabled (positives/negatives are set)
          // and the user presses it to actually execute — same
          // pattern as typing prompts fresh.
          positives = [...s.positives];
          negatives = [...s.negatives];
        }}
      />
      <button
        type="button"
        class="primary"
        onclick={reload}
        disabled={!positives.length && !negatives.length && !filename.trim() && !collections.length || loading}
        title="Run search"
      >
        Search
      </button>
    </div>
  {/if}
</section>

<section class="results">
  {#if hasSearched}
    {#if error}
      <div class="error glass">Couldn't load results: {error}</div>
    {:else}
      <PhotoGrid
        items={items}
        {loading}
        {hasMore}
        onLoadMore={loadMore}
        {onToggleFavorite}
        {onDislike}
      />
    {/if}
  {/if}
</section>

<style>
  .hero {
    /* Matches the .head width on the other grid pages (random,
       for-you, albums, similar, ...). All page-level "sections"
       span --grid-width so they line up visually. */
    width: var(--grid-width, 100%);
    max-width: 1548px;
    margin: 0 auto;
    padding: 40px 16px 28px;
    text-align: center;
  }

  .empty-prompt {
    display: inline-flex;
    align-items: center;
    gap: var(--s-2);
    padding: var(--s-2) var(--s-3);
    margin: 0 auto var(--s-3);
    background: var(--accent-soft);
    border: 1px solid var(--accent);
    border-radius: var(--r-pill);
    color: var(--fg-1);
    font-size: 14px;
  }
  .empty-prompt a {
    color: var(--accent);
    text-decoration: none;
    font-weight: 500;
  }
  .empty-prompt a:hover {
    text-decoration: underline;
  }
  .empty-prompt .dismiss {
    background: none;
    border: none;
    color: var(--fg-2);
    cursor: pointer;
    font-size: 18px;
    line-height: 1;
    padding: 0 var(--s-0);
  }
  .empty-prompt .dismiss:hover {
    color: var(--fg-1);
  }
  /* Saved-searches + Search button. Pulled out of SearchComposer
     so the action row sits below the diversity panel, not below
     the search inputs (matches the layout the user wants:
     inputs → diversity options → actions). */
  .search-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
  }
  .primary {
    height: 44px;
    padding: 0 28px;
    border-radius: var(--r-pill);
    background: var(--accent);
    color: var(--fg-on-accent);
    font-weight: 600;
    font-size: var(--fs-md);
    transition: background var(--t-fast);
    box-shadow: 0 4px 18px rgba(108,198,255,0.30);
  }
  .primary:hover { background: var(--accent-2); }
  .primary:disabled {
    background: var(--glass-1);
    color: var(--fg-3);
    box-shadow: none;
    cursor: not-allowed;
  }
  .hero h1 {
    font-size: var(--fs-3xl);
    font-weight: 600;
    margin: 0 0 6px;
    letter-spacing: -0.01em;
    line-height: var(--lh-tight);
  }
  .hero .sub {
    color: var(--fg-muted);
    margin: 0 auto 28px;
    max-width: 56ch;
    line-height: var(--lh-prose);
  }
  .hero .sub code {
    background: var(--glass-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-1);
    padding: 1px 6px;
    font-size: 0.9em;
  }
  /* Spacing inside the hero stack:
       composer (PromptChips + CollectionsChips)
         ↓ 14px
       diversity / filename controls (AdditionalFilters)
         ↓ 10px
       search actions (Saved + Search)
     These gaps are deliberate — title→subtitle is 6px (they
     read as one block), subtitle→composer is 28px (separate
     chunk), and these middle gaps give each card its own
     breathing room instead of stacking them flush. */
  .hero > :global(.filters) {
    margin-top: 14px;
  }
  .search-actions {
    margin-top: 10px;
  }
  .back-link {
    color: var(--fg-2);
    text-decoration: none;
    margin-left: 8px;
    transition: color var(--t-fast);
  }
  .back-link:hover { color: var(--fg-1); }
  /* Round‑34: "Surprise me" link in the album-search header. Same
     colour as .back-link so the two actions read as siblings,
     but no extra margin (the `·` separator handles the gap). */
  .surprise-link {
    color: var(--fg-2);
    text-decoration: none;
    transition: color var(--t-fast);
  }
  .surprise-link:hover { color: var(--fg-1); }
  .results { margin-top: 8px; }
  .error {
    padding: 14px 18px;
    color: var(--negative);
  }
</style>