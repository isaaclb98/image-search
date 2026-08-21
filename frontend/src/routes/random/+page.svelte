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
  import SearchGrid from '$lib/components/SearchGrid.svelte';
  import { toast } from '$lib/components/Toaster.svelte';

  type Item = {
    id: string;
    path?: string;
    score?: number;
    score_str?: string;
    blurhash?: string | null;
    is_favorite?: boolean;
  };

  const PAGE = 20;
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(true);

  async function refresh() {
    loading = true;
    try {
      const res = await random(PAGE);
      items = (res?.results ?? []) as Item[];
      hasMore = items.length > 0;
    } catch {
      items = [];
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  async function loadMore() {
    if (loading || !hasMore) return;
    loading = true;
    try {
      const res = await random(PAGE);
      const more = (res?.results ?? []) as Item[];
      // Dedupe against what's already on screen. With /api/random
      // returning fresh samples per call, repeats are rare but
      // possible (especially in the 60-photo demo dataset). If
      // dedupe yields zero new rows, stop the loop — the library
      // is exhausted and the sentinel stops firing.
      const seen = new Set(items.map((i) => i.id));
      const fresh = more.filter((m) => !seen.has(m.id));
      items = [...items, ...fresh];
      hasMore = fresh.length > 0;
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
      toast.show('Marked as not interested.', { kind: 'success' });
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
  }

  onMount(refresh);
</script>

<svelte:head>
  <title>Random · image-search</title>
</svelte:head>

<section class="head glass">
  <h1>Random</h1>
  <p>A smattering of what's on the shelf. Scroll for more.</p>
</section>

<section class="grid-wrap">
  <SearchGrid
    {items}
    {loading}
    {hasMore}
    onLoadMore={loadMore}
    {onToggleFavorite}
    {onDislike}
  />
</section>

<style>
  .head {
    margin: 16px 0 24px;
    padding: 22px 26px;
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .head h1 {
    font-size: var(--fs-2xl);
    font-weight: 600;
    margin: 0;
  }
  .head p { color: var(--fg-2); margin: 4px 0 0; }
  .grid-wrap { padding-top: 8px; }
</style>
