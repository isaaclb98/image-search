<script lang="ts">
  /**
   * Likes — built-in non-deletable album. Reads from /api/favorites
   * and renders them in the same grid the user-created albums use.
   *
   * "Like" / "Unlike" toggles go through PhotoGrid's
   * onToggleFavorite handler (wired below). Unlike removes the
   * photo from the grid.
   */
  import { onMount } from 'svelte';
  import {
    listFavorites,
    unlikePoint
  } from '$lib/api/endpoints';
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
  import { toast } from '$lib/components/Toaster.svelte';
  import Icon from '$lib/components/Icon.svelte';

  type Item = {
    id: string;
    path?: string;
    blurhash?: string | null;
    is_favorite?: boolean;
  };

  let items = $state<Item[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);

  async function refresh() {
    loading = true;
    try {
      const res = (await listFavorites(200)) as { results?: Item[] };
      items = (res?.results ?? []) as Item[];
    } catch (e: any) {
      error = e?.message ?? 'Failed to load likes';
    } finally {
      loading = false;
    }
  }

  async function onToggleFavorite(id: string) {
    try {
      await unlikePoint(id);
      items = items.filter((it) => it.id !== id);
      toast.show('Removed from Likes.', { kind: 'success' });
    } catch {
      toast.show('Failed to remove like.', { kind: 'error' });
    }
  }

  onMount(refresh);
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
      loading={false}
      hasMore={false}
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
    margin-bottom: 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
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
  .placeholder.empty {
    border: 1px dashed var(--glass-edge);
    border-radius: var(--r-2);
  }
  .placeholder.error { color: var(--negative); }
</style>
