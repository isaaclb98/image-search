<script lang="ts">
  /**
   * Album detail — grid of photos in this album, with a
   * "Download zip" link to /albums/{id}/download.zip and
   * a back link to /albums.
   */
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { getAlbum, removePhotoFromAlbum } from '$lib/api/endpoints';
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
  import { toast } from '$lib/components/Toaster.svelte';
  import type { AlbumDetail } from '$lib/api/endpoints';

  type Member = {
    id: string;
    point_id?: string;
    path?: string;
    url?: string;
    blurhash?: string | null;
    is_favorite?: boolean;
  };
  type Item = {
    id: string;
    path?: string;
    score_str?: string;
    blurhash?: string | null;
    is_favorite?: boolean;
  };

  let detail = $state<AlbumDetail | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  async function load() {
    const id = String($page.params.id);
    loading = true;
    try {
      detail = (await getAlbum(id)) as AlbumDetail;
    } catch (e: any) {
      error = e?.message ?? 'Failed to load album';
    } finally {
      loading = false;
    }
  }
  onMount(load);

  function items(): Item[] {
    if (!detail) return [];
    return (detail.members ?? []).map((m: Member) => ({
      id: m.point_id ?? m.id,
      path: m.path,
      blurhash: m.blurhash ?? null,
      is_favorite: m.is_favorite ?? true
    }));
  }

  /**
   * Remove a photo from THIS album. Optimistic: drop from the local
   * list immediately so the grid animates the tile out, then call
   * the DELETE endpoint. On failure, re-add + toast so the user
   * doesn't lose the action.
   *
   * The album id comes from $page.params; capture it in the closure
   * so the handler stays a one-argument fn the PhotoGrid can call
   * with just the photo id.
   */
  async function onRemoveFromAlbum(pointId: string) {
    const albumId = String($page.params.id);
    const before = detail;
    if (!before) return;
    // Optimistic remove.
    detail = {
      ...before,
      members: (before.members ?? []).filter(
        (m: Member) => (m.point_id ?? m.id) !== pointId,
      ),
      member_total: Math.max(
        0,
        (before.member_total ?? before.members?.length ?? 1) - 1,
      ),
    };
    try {
      await removePhotoFromAlbum(Number(albumId), pointId);
      toast.show('Removed from album.', { kind: 'success' });
    } catch (e: any) {
      // Restore the previous state.
      detail = before;
      toast.show(`Failed to remove: ${e?.message ?? 'unknown error'}`, {
        kind: 'error',
      });
    }
  }
</script>

<svelte:head>
  <title>{detail?.name ?? 'Album'} · Image Search</title>
</svelte:head>

<a class="back" href="/albums">← All albums</a>

{#if loading}
  <div class="placeholder">Loading…</div>
{:else if error || !detail}
  <div class="placeholder error">Couldn't load album: {error ?? 'not found'}</div>
{:else}
  <section class="head glass">
    <div>
      <h1>{detail.name}</h1>
      {#if detail.description}<p>{detail.description}</p>{/if}
      <p class="meta">{detail.member_total ?? detail.members?.length ?? 0} photos</p>
    </div>
    {#if detail.id}
      <a class="zip" href="/albums/{detail.id}/download.zip" target="_blank" rel="noopener">
        Download zip
      </a>
    {/if}
  </section>
  <section>
    <PhotoGrid
      items={items()}
      loading={false}
      hasMore={false}
      onRemove={onRemoveFromAlbum}
      removeLabel="Remove from album"
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
  }
  .head p {
    margin: 4px 0 0;
    color: var(--fg-2);
  }
  .head .meta { color: var(--fg-3); font-size: var(--fs-sm); }
  .zip {
    height: 40px;
    padding: 0 18px;
    border-radius: var(--r-pill);
    background: var(--glass-2);
    color: var(--fg-1);
    border: 1px solid var(--glass-edge-strong);
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    font-size: var(--fs-sm);
  }
  .zip:hover { background: rgba(255,255,255,0.14); }
  .placeholder {
    color: var(--fg-3);
    padding: 32px 16px;
    background: var(--glass-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-3);
    text-align: center;
  }
  .placeholder.error { color: var(--negative); }
</style>
