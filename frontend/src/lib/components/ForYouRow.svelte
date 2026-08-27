<script lang="ts">
  /**
   * ForYouRow — the small horizontal-scrolling row of 20 photos
   * shown on the Home page. Picks 20 randomly out of the top 800
   * recommended photos so each visit feels fresh.
   *
   *   - Lightweight: no infinite scroll, no context menu
   *   - Click → opens Lightbox
   *   - Like/Dislike/Most-similar wired through the Lightbox
   *     (round-5 #1: For You row Like button now works because
   *      we pass onToggleFavorite + onDislike + albums down)
   *   - Hover chrome same as SearchGrid tiles
   */
  import { onMount } from 'svelte';
  import {
    forYouFeed,
    likePoint,
    unlikePoint,
    dislikePoint,
    listAlbums,
    photoUrl
  } from '$lib/api/endpoints';
  import { pageTint } from '$lib/stores/tint';
  import { toast } from './Toaster.svelte';
  import PhotoTile from './PhotoTile.svelte';
  import Lightbox from './Lightbox.svelte';

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
      const want = 20;
      const res = await forYouFeed(poolSize);
      const pool = res?.results ?? [];
      // Pick `want` items uniformly without replacement, shuffle first.
      const shuffled = [...pool].sort(() => Math.random() - 0.5);
      items = shuffled.slice(0, want).map((it: any) => ({
        id: it.id,
        blurhash: it.blurhash ?? null,
        score_str:
          typeof it.score === 'number' ? it.score.toFixed(3) : '',
        is_favorite: !!it.is_favorite,
        is_disliked: !!it.is_disliked
      }));
    } catch {
      items = [];
    } finally {
      loading = false;
    }
    // Drive the full-viewport backdrop from the first photo (Home page
    // doesn't render a SearchGrid, so ForYouRow is the tint source here).
    if (items[0]?.id) pageTint.set(photoUrl(items[0].id));
  }

  async function loadAlbums() {
    if (albums.length > 0) return;
    try {
      const res = (await listAlbums()) as { albums?: { id: number; name: string }[] };
      albums = res?.albums ?? [];
    } catch {
      // non-fatal — right-click "Add to album" submenu will show empty
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
      // No toast (round-4 #9) — silent. Lightbox button provides
      // the visual feedback via .action:active { scale(0.96) }.
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
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
    <div class="scroller" role="list">
      {#each items as it, i (it.id)}
        <div class="cell" role="listitem">
          <PhotoTile
            pointId={it.id}
            scoreStr={it.score_str}
            blurhash={it.blurhash ?? null}
            isFavorite={!!it.is_favorite}
            isDisliked={!!it.is_disliked}
            onOpen={() => (lightboxIndex = i)}
          />
        </div>
      {/each}
    </div>
  {/if}
</section>

{#if lightboxIndex !== null && items.length}
  <Lightbox
    items={items.map((i) => ({ id: i.id, blurhash: i.blurhash ?? null, isFavorite: !!i.is_favorite, isDisliked: !!i.is_disliked }))}
    index={lightboxIndex}
    onClose={() => (lightboxIndex = null)}
    {onToggleFavorite}
    {onDislike}
    {albums}
  />
{/if}

<style>
  .row-section {
    margin-top: var(--s-5);
    padding: 0 var(--s-4, 24px);
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
    letter-spacing: 0.01em;
  }
  .more {
    color: var(--fg-2);
    font-size: var(--fs-sm);
  }
  .more:hover { color: var(--fg-1); }

  .scroller {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: var(--grid-gutter);
  }
  @media (max-width: 900px) {
    .scroller { grid-template-columns: repeat(4, 1fr); }
  }
  @media (max-width: 600px) {
    .scroller { grid-template-columns: repeat(3, 1fr); }
  }
  .cell { aspect-ratio: 1 / 1; }
  .placeholder {
    color: var(--fg-3);
    padding: 28px 16px;
    background: var(--glass-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-3);
    text-align: center;
    font-size: var(--fs-sm);
  }
  .placeholder.empty { color: var(--fg-2); }
</style>
