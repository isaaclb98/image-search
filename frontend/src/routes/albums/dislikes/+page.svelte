<script lang="ts">
  /**
   * Dislikes — built-in non-deletable album. Reads from
   * /api/dislikes and renders them in the standard grid.
   *
   * No "Download zip" button — disliking is a signal, not a
   * collection. The "−" button on each tile undoes the dislike
   * via the onDislike handler wired below; that drops the photo
   * out of the grid.
   */
  import { onMount } from 'svelte';
  import {
    listDislikes,
    undislikePoint,
    similarPhotos
  } from '$lib/api/endpoints';
  import SearchGrid from '$lib/components/SearchGrid.svelte';
  import { toast } from '$lib/components/Toaster.svelte';

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
      const res = (await listDislikes(200)) as { results?: Item[] };
      items = (res?.results ?? []) as Item[];
    } catch (e: any) {
      error = e?.message ?? 'Failed to load dislikes';
    } finally {
      loading = false;
    }
  }

  async function onDislike(id: string) {
    try {
      await undislikePoint(id);
      items = items.filter((it) => it.id !== id);
      toast.show('Removed from Dislikes.', { kind: 'success' });
    } catch {
      toast.show('Failed to remove dislike.', { kind: 'error' });
    }
  }

  async function onSimilar(id: string) {
    try {
      const res = await similarPhotos(id, 30);
      items = (res?.results ?? []) as Item[];
    } catch (e: any) {
      toast.show(`Couldn't load similar photos: ${e?.message ?? e}`, {
        kind: 'error',
      });
    }
  }

  onMount(refresh);
</script>

<svelte:head>
  <title>Dislikes · image-search</title>
</svelte:head>

<a class="back" href="/albums">← All albums</a>

<section class="head glass">
  <div>
    <h1>− Dislikes</h1>
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
    <SearchGrid {items} loading={false} hasMore={false} {onDislike} {onSimilar} />
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
