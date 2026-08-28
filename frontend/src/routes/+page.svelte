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
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
  import ForYouRow from '$lib/components/ForYouRow.svelte';
  import {
    search,
    likePoint,
    unlikePoint,
    dislikePoint
  } from '$lib/api/endpoints';
  import type { SavedSearch } from '$lib/api/endpoints';
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

  const PAGE = 20;

  // Composer state (hoisted from SearchComposer).
  let positives = $state<string[]>([]);
  let negatives = $state<string[]>([]);
  let input = $state('');
  let mode = $state<'pos' | 'neg'>('pos');
  let filename = $state('');
  let diversityMode = $state('off');
  let diversityDepth = $state('auto');
  let filtersOpen = $state(false);

  // Results state.
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(false);
  let offset = $state(0);
  let error = $state<string | null>(null);
  let ctrl: AbortController | null = null;
  let hasSearched = $state(false);
  // Round‑29: when set, the page treats this as a "search by album
  // centroid" — the SearchComposer is hidden and reload() hits the
  // centroid endpoint instead of /api/search. URL ?centroid=...
  // sets this on mount; writing back to URL is suppressed.
  let activeCentroid = $state<string | null>(null);

  function readFromUrl() {
    const q = $page.url.searchParams;
    positives = q.getAll('positives');
    negatives = q.getAll('negatives');
    filename = q.get('filename') ?? '';
    diversityMode = q.get('diversity') ?? 'off';
    diversityDepth = q.get('diversity_depth') ?? 'auto';
    filtersOpen = !!filename || diversityMode !== 'off' || diversityDepth !== 'auto';
    activeCentroid = q.get('centroid');
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
    const active =
      activeCentroid ||
      positives.length ||
      negatives.length ||
      filename.trim();
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
          centroid: activeCentroid ?? undefined
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

  async function loadMore() {
    if (loading || !hasMore) return;
    loading = true;
    try {
      const res = await search({
        positives, negatives, filename,
        diversityMode, diversityDepth,
        limit: PAGE, offset,
        centroid: activeCentroid ?? undefined
      });
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
      offset += more.length;
      hasMore = more.length >= PAGE && !!res?.has_more;
    } catch {
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
  <title>image-search</title>
</svelte:head>

<section class="hero">
  {#if activeCentroid}
    <!-- Round‑29: search-by-album mode hides the prompt composer.
         The album's centroid IS the query; there's nothing to type. -->
    <h1>Searching by album</h1>
    <p class="sub">
      Showing the photos closest to the average of <code>{activeCentroid}</code>.
      <a href="/albums" class="back-link">← Back to albums</a>
    </p>
  {:else}
    <h1>Find photos by what they look like.</h1>
    <p class="sub">
      Type what you remember — colours, moods, subjects — and pick from the
      results. Save the searches you love, like your favourites, discover
      what's nearby.
    </p>
  {/if}
  {#if !activeCentroid}
    <SearchComposer
      {positives}
      {negatives}
      {input}
      {mode}
      {filename}
      {diversityMode}
      {diversityDepth}
      {filtersOpen}
      {loading}
      onInput={(v) => (input = v)}
      onMode={(m) => (mode = m)}
      onAdd={addPrompt}
      onRemovePositive={removePositive}
      onRemoveNegative={removeNegative}
      onFilename={(v) => (filename = v)}
      onDiversityMode={(v) => (diversityMode = v)}
      onDiversityDepth={(v) => (diversityDepth = v)}
      onToggleFilters={() => (filtersOpen = !filtersOpen)}
      onSearch={reload}
      onPickSaved={(s: SavedSearch) => {
        positives = [...s.positives];
        negatives = [...s.negatives];
        reload();
      }}
    />
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

<ForYouRow />

<style>
  .hero {
    max-width: 980px;
    margin: 0 auto;
    padding: 32px 16px 12px;
    text-align: center;
  }
  .hero h1 {
    font-size: var(--fs-3xl);
    font-weight: 600;
    margin: 0 0 12px;
    letter-spacing: -0.01em;
    line-height: var(--lh-tight);
  }
  .hero .sub {
    color: var(--fg-muted);
    margin: 0 auto 24px;
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
  .back-link {
    color: var(--fg-2);
    text-decoration: none;
    margin-left: 8px;
    transition: color var(--t-fast);
  }
  .back-link:hover { color: var(--fg-1); }
  .results { margin-top: 8px; }
  .error {
    padding: 14px 18px;
    color: var(--negative);
  }
</style>