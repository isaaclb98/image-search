<!--
  ForYouRow — small section on the Home page showing a random sample
  of 20 photos from the For-You recommendation pool.

  Round‑27: thin wrapper around the canonical PhotoGrid. All the
  actual grid markup, padding, gutter, virtualisation, right-click
  context menu, and lightbox wiring now lives in PhotoGrid so the
  For-You row renders the same as search results, /random,
  /similar, /albums, etc.

  This wrapper owns only the data fetch + the section header.
-->
<script lang="ts">
  import { onMount } from 'svelte';
  import { forYouFeed, likePoint, unlikePoint, dislikePoint, listAlbums } from '$lib/api/endpoints';
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
  import { toast } from '$lib/components/Toaster.svelte';

  type Tile = {
    id: string;
    blurhash?: string | null;
    score_str?: string;
    is_favorite?: boolean;
    is_disliked?: boolean;
  };

  let items = $state<Tile[]>([]);
  let loading = $state(true);
  let lightboxIndex = $state<number | null>(null);
  let albums = $state<{ id: number; name: string }[]>([]);

  async function load() {
    loading = true;
    try {
      const poolSize = 800;
      // Target exactly 3 rows of tiles. Compute the column count
      // the CSS grid will pick at this viewport and round the
      // sample size to a multiple of it, so the last row is always
      // full (no awkward partial-row of clustered tiles on the
      // left).
      //
      // CSS: `grid-template-columns: repeat(auto-fill, minmax(180px, 1fr))`
      // with `gap: var(--grid-gutter, 20px)`. Column count =
      //   floor((containerWidth + GAP) / (COLUMN_MIN + GAP))
      // estimated from `window.innerWidth - main padding`. Note
      // `.app-main` is capped at 1600px, so the wrapper width is
      // min(viewport - 48, 1552).
      const COL_MIN = 180;
      const GAP = 20;
      const MAIN_PAD = 48; // 24px each side, matches `.app-main`
      const MAIN_MAX = 1552; // 1600px cap minus padding
      const wrapperW = Math.min(window.innerWidth - MAIN_PAD, MAIN_MAX);
      const cols = Math.max(
        1,
        Math.floor((wrapperW + GAP) / (COL_MIN + GAP))
      );
      const want = cols * 3;
      const res = await forYouFeed(poolSize);
      const pool = res?.results ?? [];
      // Pick `want` items uniformly without replacement, shuffle first.
      const shuffled = [...pool].sort(() => Math.random() - 0.5);
      items = shuffled.slice(0, want).map((it) => ({
        id: it.id,
        blurhash: it.blurhash ?? null,
        score_str:
          typeof it.score === 'number' ? it.score.toFixed(3) : '',
        is_favorite: !!it.is_favorite,
        is_disliked: !!it.is_disliked
      }));
    } catch (e) {
      console.error('ForYouRow: feed fetch failed', e);
      toast.show('Could not load recommendations.', { kind: 'error' });
    } finally {
      loading = false;
    }
  }

  async function loadAlbums() {
    try {
      const res = await listAlbums();
      albums = (res?.albums ?? []).map((a) => ({ id: a.id, name: a.name }));
    } catch {
      albums = [];
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
      // Round‑28: do NOT close the lightbox here. The user just
      // tapped Like on a photo they like; dismissing the lightbox
      // forces a context switch and breaks the flow when they're
      // paging through photos with arrow keys. The visual feedback
      // (filled heart) is enough.
    } catch {
      toast.show('Failed to update like.', { kind: 'error' });
    }
  }

  async function onDislike(id: string) {
    try {
      await dislikePoint(id);
      items = items.map((x) =>
        x.id === id ? { ...x, is_disliked: true } : x
      );
      // Round‑28: same — keep the lightbox open. The user can close
      // it themselves or page to the next photo.
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
  }

  function onPhotoOpen(item: Tile) {
    lightboxIndex = items.findIndex((x) => x.id === item.id);
  }

  function onClose() {
    lightboxIndex = null;
  }

  onMount(() => {
    load();
    loadAlbums();
  });
</script>

<section class="row-section">
  <header class="head">
    <h2>For you</h2>
    <a class="more" href="/for-you">See all →</a>
  </header>

  {#if loading && items.length === 0}
    <p class="placeholder empty">Loading recommendations…</p>
  {:else if items.length === 0}
    <p class="placeholder empty">No recommendations yet — like or dislike a few photos to seed the signal.</p>
  {:else}
    <PhotoGrid
      {items}
      {loading}
      hasMore={false}
      {albums}
      {onToggleFavorite}
      {onDislike}
      {onPhotoOpen}
      bind:lightboxIndex
    />
  {/if}
</section>

<style>
  .row-section {
    margin-top: var(--s-5);
    /* No horizontal padding — `.app-main` already provides 24px
       of side padding for the whole page. Adding it here too
       double-insets this section (48px total) and makes it look
       narrower than the search grid above it. */
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 4px 12px;
  }
  .head h2 {
    font-size: var(--fs-xl);
    font-weight: 600;
    margin: 0;
    letter-spacing: -0.01em;
  }
  .more {
    color: var(--fg-2);
    text-decoration: none;
    font-size: var(--fs-sm);
    transition: color var(--t-fast);
  }
  .more:hover { color: var(--fg-1); }

  .placeholder {
    color: var(--fg-3);
    padding: 28px 16px;
    background: var(--glass-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-3);
    text-align: center;
    font-size: var(--fs-sm);
  }
</style>