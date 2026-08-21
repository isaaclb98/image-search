<script lang="ts">
  /**
   * Random — pick N random photos. Loads more on scroll like
   * SearchGrid. Like / Dislike toggles work in-place; the tile
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

  const PAGE = 60;
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(true);

  async function refresh() {
    loading = true;
    try {
      const res = await random(PAGE);
      items = (res?.results ?? []) as Item[];
      hasMore = items.length >= PAGE;
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
      const seen = new Set(items.map((i) => i.id));
      const fresh = more.filter((m) => !seen.has(m.id));
      items = [...items, ...fresh];
      hasMore = more.length >= PAGE;
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
  <p>A smattering of what's on the shelf.</p>
  <button type="button" class="reroll" onclick={refresh} disabled={loading}>
    {loading ? 'Rolling…' : 'Roll again'}
  </button>
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
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 12px 16px;
  }
  .head h1 {
    font-size: var(--fs-2xl);
    font-weight: 600;
    margin: 0;
    grid-column: 1;
  }
  .head p {
    color: var(--fg-2);
    margin: 0;
    grid-column: 1;
  }
  .reroll {
    grid-row: 1 / span 2;
    grid-column: 2;
    align-self: center;
    height: 40px;
    padding: 0 20px;
    border-radius: var(--r-pill);
    background: var(--glass-2);
    color: var(--fg-1);
    border: 1px solid var(--glass-edge-strong);
    transition: background var(--t-fast);
  }
  .reroll:hover { background: rgba(255,255,255,0.14); }
  .reroll:disabled { opacity: 0.5; pointer-events: none; }
  .grid-wrap { padding-top: 8px; }
</style>
