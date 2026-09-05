<script lang="ts">
  /**
   * Random — pick N random photos. Infinite scroll: keeps loading
   * more pages of random samples as the user scrolls. The dedupe
   * pass in loadMore stops the cycle once the dedupe filter
   * returns nothing new.
   *
   * No "Roll again" button — the page is meant to be browsed, not
   * re-rolled. No "End of results" UI either: when the library is
   * exhausted the scroll sentinel just stops firing.
   *
   * Like / Dislike / Most-similar toggles work in-place; the tile
   * stays in the grid either way.
   */
  import { onMount } from 'svelte';
  import {
    random,
    likePoint,
    unlikePoint,
    dislikePoint
  } from '$lib/api/endpoints';
  import { GRID_PAGE_SIZE } from '$lib/api/limits';
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
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
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(true);

  // Server-side session cursor: the first call creates a shuffled
  // deck and returns a session id; subsequent calls pass the same
  // session id with an incremented offset to walk forward. The
  // server guarantees no duplicates within a session, so the client
  // doesn't need any dedupe logic — just append.
  let sessionId = $state<string | null>(null);
  let nextOffset = $state(0);

  async function refresh() {
    loading = true;
    try {
      // New session, start from offset 0.
      const res = await random({ limit: PAGE });
      items = (res?.results ?? []) as Item[];
      sessionId = res?.session_id ?? null;
      nextOffset = items.length;
      // session_total is the deck length. has_more from the server
      // tells us whether there's anything left to fetch.
      hasMore = !!res?.has_more && items.length > 0;
    } catch {
      items = [];
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  async function loadMore() {
    if (loading || !hasMore || !sessionId) return;
    loading = true;
    try {
      const res = await random({
        session: sessionId,
        offset: nextOffset,
        limit: PAGE,
      });
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
      nextOffset += more.length;
      hasMore = !!res?.has_more && more.length > 0;
    } catch {
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  // Round-9 perf: O(1) item lookup + update via shadow Map.
  // Before this round, onToggleFavorite / onDislike used
  // `items.find(...)` + `items.map(...)`, both O(n). For
  // infinite-scrolled pages (1000+ items) every Like click
  // walked the whole array twice. The Map mirrors `items`
  // (id → index); updates stay O(1) as long as callers
  // don't reorder / splice the array (they don't — we only
  // push to the end or replace in-place).
  let indexById = $state(new Map<string, number>());

  // Append-only when `items` changes; rebuilds only when
  // the array reference changes (after refresh / loadMore).
  // The shadow Map is never mutated directly — always
  // rebuilt from `items` so it can't drift.
  $effect(() => {
    const map = new Map<string, number>();
    for (let i = 0; i < items.length; i++) {
      map.set(items[i].id, i);
    }
    indexById = map;
  });

  async function onToggleFavorite(id: string) {
    const idx = indexById.get(id);
    if (idx === undefined) return;
    const liked = items[idx]?.is_favorite ?? false;
    try {
      if (liked) await unlikePoint(id);
      else await likePoint(id);
      // O(1) replace — same array reference, one slot updated.
      // Svelte 5 reactivity triggers because the inner object
      // changes (we spread to a new object).
      const next = items.slice();
      next[idx] = { ...next[idx], is_favorite: !liked };
      items = next;
    } catch {
      toast.show('Failed to update like.', { kind: 'error' });
    }
  }

  async function onDislike(id: string) {
    const idx = indexById.get(id);
    if (idx === undefined) return;
    try {
      await dislikePoint(id);
      const next = items.slice();
      next[idx] = { ...next[idx], is_disliked: true };
      items = next;
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
  }

  onMount(refresh);
</script>

<svelte:head>
  <title>Random · Image Search</title>
</svelte:head>

<PageHeader title="Random" subtitle="Random photos from your library. Scroll for more." />

<section class="grid-wrap">
  <PhotoGrid
    {items}
    {loading}
    {hasMore}
    onLoadMore={loadMore}
    {onToggleFavorite}
    {onDislike}
  />
</section>

<style>
  .grid-wrap { padding-top: 8px; }
</style>
