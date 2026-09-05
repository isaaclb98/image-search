<script lang="ts">
  /**
   * Album detail — grid of photos in this album, with a
   * "Download zip" link to /albums/{id}/download.zip and
   * a back link to /albums.
   *
   * Infinite scroll: walks /api/albums/{id}?limit=&offset= in
   * batches of GRID_PAGE_SIZE so the UI keeps working no matter
   * how large the album grows. The AlbumDetailResponse carries
   * member_total which we compare against the running member
   * count to drive `has_more`.
   */
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { getAlbum, removePhotoFromAlbum } from '$lib/api/endpoints';
  import { GRID_PAGE_SIZE } from '$lib/api/limits';
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
  import { toast } from '$lib/components/Toaster.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
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

  const PAGE = GRID_PAGE_SIZE;

  let detail = $state<AlbumDetail | null>(null);
  // Accumulated members across paged /api/albums/{id} responses.
  // Each response carries `members` (the slice for this batch)
  // and `member_total` (the album-wide total) — we append the
  // slices here and compare lengths to drive `has_more`.
  let members = $state<Member[]>([]);
  let loading = $state(true);
  let loadingMore = $state(false);
  let error = $state<string | null>(null);
  let offset = $state(0);
  let hasMore = $state(false);

  async function load() {
    const id = String($page.params.id);
    loading = true;
    try {
      const res = (await getAlbum(id, PAGE, 0)) as AlbumDetail;
      detail = res;
      members = (res.members ?? []) as Member[];
      offset = members.length;
      const total = res.member_total ?? members.length;
      hasMore = offset < total && members.length >= PAGE;
      error = null;
    } catch (e: any) {
      error = e?.message ?? 'Failed to load album';
    } finally {
      loading = false;
    }
  }

  async function loadMore() {
    if (loading || loadingMore || !hasMore || !detail) return;
    const id = String($page.params.id);
    loadingMore = true;
    try {
      // Reuse the album-detail endpoint with the next offset —
      // it already returns metadata + a member slice in one
      // round-trip, and the first call cached the album's name/
      // description/total in `detail` so we just take `members`.
      const res = (await getAlbum(id, PAGE, offset)) as AlbumDetail;
      const more = (res.members ?? []) as Member[];
      members = [...members, ...more];
      offset = members.length;
      const total = res.member_total ?? members.length;
      hasMore = offset < total && more.length >= PAGE;
    } catch {
      // Leave the existing list intact; the user can keep paging
      // — losing scroll progress on a transient error is worse
      // than a stuck spinner.
    } finally {
      loadingMore = false;
    }
  }

  onMount(load);

  function items(): Item[] {
    return members.map((m: Member) => ({
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
   * The album id comes from $page.params; the photo id from the
   * tile's `id` prop.
   */
  async function onRemoveFromAlbum(pointId: string) {
    const albumId = $page.params.id;
    const before = members;
    members = members.filter(
      (m) => (m.point_id ?? m.id) !== pointId,
    );
    // Reflect the local removal in the cached total so the
    // header counter ("N photos") stays accurate.
    if (detail && typeof detail.member_total === 'number') {
      detail = {
        ...detail,
        member_total: Math.max(0, detail.member_total - 1),
      };
    }
    try {
      await removePhotoFromAlbum(Number(albumId), pointId);
      toast.show('Removed from album.', { kind: 'success' });
    } catch (e: any) {
      // Restore the previous state.
      members = before;
      if (detail && typeof detail.member_total === 'number') {
        detail = {
          ...detail,
          member_total: detail.member_total + 1,
        };
      }
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
  <PageHeader
    title={detail.name}
    subtitle={detail.description}
    meta="{detail.member_total ?? members.length} photos"
  >
    {#snippet actions()}
      {#if detail && detail.id}
        <a class="zip" href="/albums/{detail.id}/download.zip" target="_blank" rel="noopener">
          Download zip
        </a>
      {/if}
    {/snippet}
  </PageHeader>
  <section>
    <PhotoGrid
      items={items()}
      loading={loadingMore}
      {hasMore}
      onLoadMore={loadMore}
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
  .zip {
    padding: 8px 16px;
    border-radius: var(--r-pill);
    background: var(--accent);
    color: #fff;
    text-decoration: none;
    font-weight: 500;
  }
  .zip:hover { background: var(--accent-strong); }
  .placeholder {
    padding: 32px 24px;
    text-align: center;
    color: var(--fg-2);
  }
  .placeholder.error { color: var(--accent); }
</style>