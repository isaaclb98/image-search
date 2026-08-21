<script lang="ts">
  /**
   * For You — full feed page (vs the Home page which shows a
   * 20-row subset). Same backend endpoint, larger page size.
   *
   * Note: previously had a "Reset signal" button that wiped
   * likes + dislikes. The user-facing bug list explicitly
   * removed Reset; reset is now an admin-only concern
   * (POST /api/for-you/reset still exists but is no longer
   * surfaced in the UI).
   */
  import { onMount } from 'svelte';
  import {
    forYouFeed,
    likePoint,
    unlikePoint,
    dislikePoint,
    similarPhotos
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

  const PAGE = 40;
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(false);
  let seen = $state<string[]>([]);

  async function refresh() {
    loading = true;
    try {
      const res = await forYouFeed(PAGE);
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
      const res = await forYouFeed(PAGE);
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
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

  async function onSimilar(id: string) {
    try {
      const res = await similarPhotos(id, 30);
      items = (res?.results ?? []) as Item[];
      hasMore = items.length >= PAGE;
    } catch (e: any) {
      toast.show(`Couldn't load similar photos: ${e?.message ?? e}`, {
        kind: 'error',
      });
    }
  }

  onMount(refresh);
</script>

<svelte:head>
  <title>For You · image-search</title>
</svelte:head>

<section class="head glass">
  <div>
    <h1>For you</h1>
    <p>Tuned by your saves, dislikes, and searches.</p>
  </div>
</section>

<section>
  <SearchGrid
    {items}
    {loading}
    {hasMore}
    onLoadMore={loadMore}
    {onToggleFavorite}
    {onDislike}
    {onSimilar}
  />
</section>

<style>
  .head {
    margin: 16px 0 24px;
    padding: 22px 26px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .head h1 {
    font-size: var(--fs-2xl);
    font-weight: 600;
    margin: 0;
  }
  .head p { color: var(--fg-2); margin: 4px 0 0; }
</style>
