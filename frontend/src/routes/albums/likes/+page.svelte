<script lang="ts">
  /**
   * Likes — built-in non-deletable album. Reads from /api/favorites
   * and renders them in the same grid the user-created albums use.
   *
   * "Like" / "Unlike" toggles go through PhotoGrid's
   * onToggleFavorite handler (wired below). Unlike removes the
   * photo from the grid.
   *
   * Infinite scroll: walks /api/favorites?limit=&offset=&as_results=1
   * in batches of GRID_PAGE_SIZE so the UI keeps working no
   * matter how large the Likes album grows. The backend's
   * SearchResponse envelope carries `has_more` which we propagate
   * to PhotoGrid's intersection-observer sentinel.
   */
  import { onMount } from 'svelte';
  import {
    listFavorites,
    unlikePoint
  } from '$lib/api/endpoints';
  import { GRID_PAGE_SIZE } from '$lib/api/limits';
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
  import { toast } from '$lib/components/Toaster.svelte';
  import Icon from '$lib/components/Icon.svelte';

  type Item = {
    id: string;
    path?: string;
    blurhash?: string | null;
    is_favorite?: boolean;
  };

  const PAGE = GRID_PAGE_SIZE;

  let items = $state<Item[]>([]);
  let loading = $state(true);
  let loadingMore = $state(false);
  let error = $state<string | null>(null);
  let offset = $state(0);
  let hasMore = $state(false);

  async function load() {
    loading = true;
    try {
      const res = (await listFavorites(PAGE, 0)) as {
        results?: Item[];
        has_more?: boolean;
      };
      items = (res?.results ?? []) as Item[];
      offset = items.length;
      hasMore = !!res?.has_more && items.length >= PAGE;
      error = null;
    } catch (e: any) {
      error = e?.message ?? 'Failed to load likes';
    } finally {
      loading = false;
    }
  }

  async function loadMore() {
    if (loading || loadingMore || !hasMore) return;
    loadingMore = true;
    try {
      const res = (await listFavorites(PAGE, offset)) as {
        results?: Item[];
        has_more?: boolean;
      };
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
      offset += more.length;
      hasMore = !!res?.has_more && more.length >= PAGE;
    } catch {
      // Leave the existing list intact; the user can keep paging
      // — losing scroll progress on a transient error is worse
      // than a stuck spinner.
    } finally {
      loadingMore = false;
    }
  }

  // Round-9 perf: O(1) item lookup + update via shadow Map.
  // The .filter() we replaced was O(n) per Unlike click — fine
  // for a 50-item list but noticeable at 1000+ items on a power
  // user's Likes album. The Map mirrors `items`; removals use
  // `splice(idx, 1)` for true O(1).
  let indexById = $state(new Map<string, number>());
  $effect(() => {
    const map = new Map<string, number>();
    for (let i = 0; i < items.length; i++) {
      map.set(items[i].id, i);
    }
    indexById = map;
  });

  async function onToggleFavorite(id: string) {
    // Optimistic: drop from the local list so the grid animates
    // the tile out, then call the API. On failure, restore + toast
    // so the user doesn't lose the action.
    const idx = indexById.get(id);
    if (idx === undefined) return;
    const before = items;
    // O(1) splice: build a new array minus one element.
    const next = items.slice();
    next.splice(idx, 1);
    items = next;
    try {
      await unlikePoint(id);
      toast.show('Removed from Likes.', { kind: 'success' });
    } catch (e: any) {
      items = before;
      toast.show(`Failed to remove: ${e?.message ?? 'unknown error'}`, {
        kind: 'error',
      });
    }
  }

  onMount(load);
</script>

<svelte:head>
  <title>Likes · Image Search</title>
</svelte:head>

<a class="back" href="/albums">← All albums</a>

<section class="head glass">
  <div>
    <h1>
      <Icon name="heart-filled" size={24} />
      <span>Likes</span>
    </h1>
    <p>Photos you've liked. Built-in, always here.</p>
    <p class="meta">{items.length} photo{items.length === 1 ? '' : 's'}</p>
  </div>
</section>

{#if loading}
  <div class="placeholder">Loading…</div>
{:else if error}
  <div class="placeholder error">Couldn't load likes: {error}</div>
{:else if items.length === 0}
  <div class="placeholder empty">
    No likes yet. Tap the heart on any photo to save it here.
  </div>
{:else}
  <section>
    <PhotoGrid
      {items}
      loading={loadingMore}
      {hasMore}
      onLoadMore={loadMore}
      {onToggleFavorite}
      onRemove={onToggleFavorite}
      removeLabel="Unlike"
    />
  </section>
{/if}

<style>
  .back {
    display: inline-block;
    margin: 12px 0 18px;
    color: var(--fg-2);
  }
  .back:hover { color: var(--fg-1); }
  .head {
    padding: 22px 26px;
    margin: 16px auto 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    width: var(--grid-width, 100%);
    max-width: 100%;
  }
  .head h1 {
    margin: 0;
    font-size: var(--fs-2xl);
    font-weight: 600;
    /* Icon + label on one row, vertically aligned. The icon's
       own viewBox keeps it optically centered. */
    display: inline-flex;
    align-items: center;
    gap: 10px;
  }
  .head p {
    margin: 4px 0 0;
    color: var(--fg-2);
  }
  .head .meta { color: var(--fg-3); font-size: var(--fs-sm); }
  .placeholder {
    padding: 32px 24px;
    text-align: center;
    color: var(--fg-2);
  }
  .placeholder.error { color: var(--accent); }
  .placeholder.empty { color: var(--fg-3); }
</style>