<script lang="ts">
  /**
   * Most-similar photos for a given point ID. Shows up to 100
   * nearest neighbours in the SigLIP2 embedding space. Reached by
   * clicking "Most similar" in the Lightbox; closes the lightbox
   * and navigates here.
   *
   * Round-5 #4: load all 100 in one shot, then show as a grid.
   * Backend /api/similar accepts limit up to 100, so there's no
   * need to page. We fetch the full set on mount; the page count
   * text dynamically reflects what's been loaded.
   */
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import {
    similarPhotos,
    likePoint,
    unlikePoint,
    dislikePoint
  } from '$lib/api/endpoints';
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
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

  const MAX_TOTAL = 100;
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);

  async function refresh() {
    const id = String($page.params.id);
    loading = true;
    error = null;
    try {
      // Fetch the full MAX_TOTAL in one call — the endpoint is a
      // vector top-k and there's no "next page" semantics.
      const res = await similarPhotos(id, MAX_TOTAL);
      items = (res?.results ?? []) as Item[];
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
      // No toast (round-4 #9). Mark disliked so the lightbox
      // button stays lit until navigation.
      items = items.map((x) =>
        x.id === id ? { ...x, is_disliked: true } : x
      );
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
  }

  onMount(refresh);
  // Re-fetch whenever the route param changes. Without this, the
  // page component is reused across navigations from /similar/A to
  // /similar/B (same dynamic route) and the user would still see
  // the previous photo's results — a manual reload was needed.
  $effect(() => {
    const id = String($page.params.id);
    if (!id) return;
    refresh();
  });
</script>

<svelte:head>
  <title>Most similar · Image Search</title>
</svelte:head>

<button
  type="button"
  class="back"
  onclick={() => (history.length > 1 ? history.back() : goto('/random'))}
>
  ← Back
</button>

<section class="head glass">
  <h1>Most similar</h1>
  <p>
    {#if items.length === 0}
      Up to {MAX_TOTAL} nearest neighbours, ranked by visual closeness.
    {:else}
      {items.length} of up to {MAX_TOTAL} nearest neighbours, ranked by visual closeness.
    {/if}
  </p>
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
    <PhotoGrid
      {items}
      {loading}
      hasMore={false}
      onLoadMore={() => {}}
      {onToggleFavorite}
      {onDislike}
    />
  </section>
{/if}

<style>
  .back {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin: 12px 0 18px;
    color: var(--fg-2);
    background: transparent;
    border: 0;
    padding: 0;
    cursor: pointer;
    font-size: var(--fs-sm);
  }
  .back:hover { color: var(--fg-1); }

  .head {
    margin: 0 auto 16px;
    padding: 22px 26px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
    width: var(--grid-width, 100%);
    max-width: 100%;
  }
  .head h1 {
    margin: 0;
    font-size: var(--fs-2xl);
    font-weight: 600;
  }
  .head p {
    margin: 4px 0 0;
    color: var(--fg-2);
    flex-basis: 100%;
  }

  .placeholder {
    color: var(--fg-3);
    padding: 28px 16px;
    background: var(--glass-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-3);
    text-align: center;
    font-size: var(--fs-sm);
  }
  .placeholder.empty,
  .placeholder.error { color: var(--fg-2); }
</style>
