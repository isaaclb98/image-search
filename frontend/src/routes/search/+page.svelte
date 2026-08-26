<script lang="ts">
  /**
   * Search page. URL � state sync:
   *   - On mount, read URL params into local state, fire a search.
   *   - When state changes, write back to the URL.
   *   - Search runs ONLY on explicit Search button clicks
   *     (issue #6). Auto-reload on state changes was removed.
   *
   * SearchComposer is a pure UI child — the Search page owns the
   * truth (positives/negatives/filename/diversity/input/mode).
   */
  import { page } from '$app/stores';
  import { onMount, tick } from 'svelte';
  import { browser } from '$app/environment';
  import SearchComposer from '$lib/components/SearchComposer.svelte';
  import SearchGrid from '$lib/components/SearchGrid.svelte';
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

  // Hoisted controls state.
  let positives = $state<string[]>([]);
  let negatives = $state<string[]>([]);
  let input = $state('');
  let mode = $state<'pos' | 'neg'>('pos');
  let filename = $state('');
  let diversityMode = $state('off');
  let diversityDepth = $state('auto');
  let filtersOpen = $state(false);

  // Results.
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(false);
  let offset = $state(0);
  let error = $state<string | null>(null);
  let ctrl: AbortController | null = null;

  function readFromUrl() {
    const q = $page.url.searchParams;
    positives = q.getAll('positives');
    negatives = q.getAll('negatives');
    filename = q.get('filename') ?? '';
    diversityMode = q.get('diversity') ?? 'off';
    diversityDepth = q.get('diversity_depth') ?? 'auto';
    filtersOpen = !!filename || diversityMode !== 'off' || diversityDepth !== 'auto';
  }

  function writeToUrl() {
    if (!browser) return;
    const qs = new URLSearchParams();
    positives.forEach((p) => qs.append('positives', p));
    negatives.forEach((n) => qs.append('negatives', n));
    if (filename) qs.set('filename', filename);
    if (diversityMode !== 'off') qs.set('diversity', diversityMode);
    if (diversityDepth && diversityDepth !== 'auto') qs.set('diversity_depth', diversityDepth);
    const next = qs.toString();
    if ($page.url.search.replace(/^\?/, '') !== next) {
      history.replaceState(history.state, '', `/search${next ? '?' + next : ''}`);
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

    const active =
      positives.length || negatives.length || filename.trim();
    if (!active) {
      loading = false;
      return;
    }
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
          offset: 0
        },
        ctrl.signal
      );
      items = (res?.results ?? []) as Item[];
      offset = items.length;
      hasMore = items.length >= PAGE && !!res?.has_more;
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        error = e?.message ?? String(e);
      }
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
        limit: PAGE, offset
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

  /** Toggle Like / Unlike — used by both the Lightbox and right-click menu. */
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

  /** Mark as Dislike — adds to dislikes; unlike happens via the Likes page. */
  async function onDislike(id: string) {
    try {
      await dislikePoint(id);
      // Mark as disliked so the lightbox button stays lit
      // (round-5 #3 — visual feedback on Dislike).
      items = items.map((x) =>
        x.id === id ? { ...x, is_disliked: true } : x
      );
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
  }

  onMount(async () => {
    readFromUrl();
    await tick();
    reload();
  });

  // URL sync only — no auto-reload. Searches fire only on explicit
  // Search button clicks (issue #6). State can change (typing a
  // prompt, picking a saved search, toggling diversity) without
  // hitting /api/search until the user actually clicks Search.
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
  <title>Search · image-search</title>
</svelte:head>

<header class="search-head">
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
    }}
  />
</header>

<section class="results">
  {#if error}
    <div class="error glass">Couldn't load results: {error}</div>
  {:else}
    <SearchGrid
      items={items}
      {loading}
      {hasMore}
      onLoadMore={loadMore}
      {onToggleFavorite}
      {onDislike}
    />
  {/if}
</section>

<style>
  /* Scrolls naturally with the page (no sticky). The user wanted
     the search menu out of the way when reading results; the
     scroll-to-top button at bottom-right is the way back up. */
  .search-head {
    padding: 16px 0 12px;
    background: linear-gradient(180deg, var(--bg-0) 70%, rgba(14,15,20,0));
  }
  .results { margin-top: 8px; }
  .error {
    padding: 14px 18px;
    color: var(--negative);
  }
</style>
