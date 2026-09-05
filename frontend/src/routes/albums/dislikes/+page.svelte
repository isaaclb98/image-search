<script lang="ts">
  /**
   * Dislikes — built-in non-deletable album. Reads from
   * /api/dislikes and renders them in the standard grid.
   *
   * No "Download zip" button — disliking is a signal, not a
   * collection. The "−" button on each tile undoes the dislike
   * via the onDislike handler wired below; that drops the photo
   * out of the grid.
   *
   * Infinite scroll: walks /api/dislikes?limit=&offset=&as_results=1
   * in batches of GRID_PAGE_SIZE so the UI keeps working no
   * matter how large the Dislikes album grows. The backend's
   * SearchResponse envelope carries `has_more` which we propagate
   * to PhotoGrid's intersection-observer sentinel.
   */
  import { onMount } from 'svelte';
  import {
    listDislikes,
    undislikePoint
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

  async function refresh() {
    loading = true;
    try {
      const res = (await listDislikes(PAGE, 0)) as {
        results?: Item[];
        has_more?: boolean;
      };
      items = (res?.results ?? []) as Item[];
      offset = items.length;
      hasMore = !!res?.has_more && items.length >= PAGE;
      error = null;
    } catch (e: any) {
      error = e?.message ?? 'Failed to load dislikes';
    } finally {
      loading = false;
    }
  }

  async function loadMore() {
    if (loading || loadingMore || !hasMore) return;
    loadingMore = true;
    try {
      const res = (await listDislikes(PAGE, offset)) as {
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

  async function onDislike(id: string) {
    // Optimistic: drop from the local list so the grid animates
    // the tile out, then call the API. On failure, restore + toast
    // so the user doesn't lose the action.
    const before = items;
    items = items.filter((it) => it.id !== id);
    try {
      await undislikePoint(id);
      toast.show('Removed from Dislikes.', { kind: 'success' });
    } catch (e: any) {
      items = before;
      toast.show(`Failed to remove: ${e?.message ?? 'unknown error'}`, {
        kind: 'error',
      });
    }
  }

  onMount(refresh);
</script>

<svelte:head>
  <title>Dislikes · Image Search</title>
</svelte:head>

<a class="back" href="/albums">← All albums</a>

<section class="head glass">
  <div>
    <h1>
      <Icon name="minus" size={24} />
      <span>Dislikes</span>
    </h1>
    <p>Photos you've marked as not interested. Built-in, always here.</p>
    <p class="meta">{items.length} photo{items.length === 1 ? '' : 's'}</p>
  </div>
</section>

{#if loading}
  <div class="placeholder">Loading…</div>
{:else if error}
  <div class="placeholder error">Couldn't load dislikes: {error}</div>
{:else if items.length === 0}
  <div class="placeholder empty">
    No dislikes yet. Tap the minus on any photo to mark it.
  </div>
{:else}
  <section>
    <PhotoGrid
      {items}
      loading={loadingMore}
      {hasMore}
      onLoadMore={loadMore}
      {onDislike}
      onRemove={onDislike}
      removeLabel="Remove dislike"
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