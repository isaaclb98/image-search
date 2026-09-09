<script lang="ts">
  /**
   * For You — like /random but the pool is constrained to the top
   * 1% (default) of library ranked by relevance-to-taste.
   *
   * On page mount we generate a fresh random `seed` and pass it
   * to every /api/for-you/feed request within this page-mount.
   * The server caches the shuffled pool keyed on
   * (fav_ids, dis_ids, top_pct, seed). Same seed → same shuffle
   * (so paginated scroll walks forward through page=0, page=1,
   * ... with no overlap). A new seed (new mount) → fresh
   * shuffle, so reloading the page always shows a different
   * set of photos.
   */
  import { onMount } from 'svelte';
  import { forYouFeed, likePoint, unlikePoint, dislikePoint } from '$lib/api/endpoints';
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
  let nextPage = $state(0);
  // One seed per page-mount. Refresh the page → new seed → fresh shuffle.
  // Scroll within the mount → same seed → walk forward through it.
  const mountSeed = Math.random().toString(36).slice(2);

  async function refresh() {
    loading = true;
    nextPage = 0;
    try {
      const res = await forYouFeed({ limit: PAGE, seed: mountSeed });
      items = (res?.results ?? []) as Item[];
      nextPage = 1;
      // session_total is the pool size; has_more means more pages.
      hasMore = !!res?.has_more && items.length > 0;
    } catch {
      items = [];
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  async function loadMore(signal?: AbortSignal) {
    if (loading || !hasMore) return;
    loading = true;
    try {
      const res = await forYouFeed({
        page: nextPage,
        limit: PAGE,
        seed: mountSeed,
        signal,
      });
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
      nextPage += 1;
      hasMore = !!res?.has_more && more.length > 0;
    } catch (e) {
      if (signal?.aborted) return; // clean cancel from pre-fetch retrigger
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  // Round-9 perf: O(1) item lookup + update via shadow Map.
  let indexById = $state(new Map<string, number>());

  $effect(() => {
    const m = new Map<string, number>();
    for (let i = 0; i < items.length; i++) m.set(items[i].id, i);
    indexById = m;
  });

  function onToggleFavorite(id: string) {
    const idx = indexById.get(id);
    if (idx === undefined) return;
    const wasFavorite = !!items[idx].is_favorite;
    items[idx] = { ...items[idx], is_favorite: !wasFavorite };
    (async () => {
      try {
        if (wasFavorite) await unlikePoint(id);
        else await likePoint(id);
      } catch {
        // rollback
        items[idx] = { ...items[idx], is_favorite: wasFavorite };
        toast.show('Failed to update like.', { kind: 'error' });
      }
    })();
  }

  async function onDislike(id: string) {
    try {
      await dislikePoint(id);
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
  }

  onMount(() => {
    refresh();
  });
</script>

<PageHeader
  title="For you"
  subtitle="A random walk through photos matched to your taste."
/>

<PhotoGrid
  items={items as any}
  {hasMore}
  {loading}
  onLoadMore={loadMore}
  onToggleFavorite={onToggleFavorite}
  onDislike={onDislike}
/>
