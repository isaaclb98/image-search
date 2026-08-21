<script lang="ts">
  /**
   * Most-similar photos for a given point ID. Shows up to 100
   * nearest neighbours in the SigLIP2 embedding space. Reached by
   * clicking "Most similar" in the Lightbox; closes the lightbox
   * and navigates here.
   */
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import {
    similarPhotos,
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
  const MAX_TOTAL = 100;
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(false);
  let error = $state<string | null>(null);

  async function refresh() {
    const id = String($page.params.id);
    loading = true;
    error = null;
    try {
      const res = await similarPhotos(id, PAGE);
      items = (res?.results ?? []) as Item[];
      // Hard cap at 100 — backend's limit is also 200, but we
      // requested only PAGE. hasMore stays false because the
      // similar-photos endpoint returns the top-k vector search
      // — there's no "next page" semantics for nearest neighbours.
      hasMore = false;
    } catch (e: any) {
      error = e?.message ?? 'Failed to load similar photos';
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
  // Re-fetch if the route param changes (e.g., nav between two
  // /similar/{id} pages without remount).
  $effect(() => {
    void $page.params.id;
    if (items.length === 0 && !loading) refresh();
  });
</script>

<svelte:head>
  <title>Most similar · image-search</title>
</svelte:head>

<a class="back" href="javascript:history.length > 1 ? history.back() : '/random'">
  ← Back
</a>

<section class="head glass">
  <h1>Most similar</h1>
  <p>{items.length} of up to {MAX_TOTAL} nearest neighbours, ranked by visual closeness.</p>
</section>

{#if loading && items.length === 0}
  <div class="placeholder">Finding visually similar photos…</div>
{:else if error}
  <div class="placeholder error">Couldn't load similar photos: {error}</div>
{:else if items.length === 0}
  <div class="placeholder empty">
    No similar photos found for this image.
  </div>
{:else}
  <section>
    <SearchGrid
      {items}
      {loading}
      {hasMore}
      onLoadMore={() => {}}
      {onToggleFavorite}
      {onDislike}
    />
  </section>
{/if}

<style>
  .back {
    display: inline-block;
    margin: 12px 0 18px;
    color: var(--fg-2);
    background: transparent;
    border: 0;
    padding: 0;
    cursor: pointer;
    font: inherit;
  }
  .back:hover { color: var(--fg-1); }
  .head {
    margin: 16px 0 24px;
    padding: 22px 26px;
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .head h1 {
    margin: 0;
    font-size: var(--fs-2xl);
    font-weight: 600;
  }
  .head p {
    margin: 4px 0 0;
    color: var(--fg-2);
  }
  .placeholder {
    padding: 32px 24px;
    text-align: center;
    color: var(--fg-2);
  }
  .placeholder.empty {
    border: 1px dashed var(--glass-edge);
    border-radius: var(--r-2);
  }
  .placeholder.error { color: var(--negative); }
</style>
