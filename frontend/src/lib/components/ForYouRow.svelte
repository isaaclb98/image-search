<script lang="ts">
  /**
   * ForYouRow — the small horizontal-scrolling row of 20 photos
   * shown on the Home page. Picks 20 randomly out of the top 800
   * recommended photos so each visit feels fresh.
   *
   *   - Lightweight: no infinite scroll, no context menu
   *   - Click → opens Lightbox
   *   - Hover chrome same as SearchGrid tiles
   */
  import { onMount } from 'svelte';
  import { forYouFeed } from '$lib/api/endpoints';
  import { photoUrl } from '$lib/api/endpoints';
  import { pageTint } from '$lib/stores/tint';
  import PhotoTile from './PhotoTile.svelte';
  import Lightbox from './Lightbox.svelte';

  type Tile = {
    id: string;
    blurhash?: string | null;
    score_str?: string;
    is_favorite?: boolean;
  };

  let items = $state<Tile[]>([]);
  let loading = $state(true);
  let lightboxIndex = $state<number | null>(null);

  async function load() {
    loading = true;
    try {
      const poolSize = 800;
      const want = 20;
      const res = await forYouFeed(poolSize);
      const pool = res?.results ?? [];
      // Pick `want` items uniformly without replacement, suffle first.
      const shuffled = [...pool].sort(() => Math.random() - 0.5);
      items = shuffled.slice(0, want).map((it: any) => ({
        id: it.id,
        blurhash: it.blurhash ?? null,
        score_str:
          typeof it.score === 'number' ? it.score.toFixed(3) : '',
        is_favorite: !!it.is_favorite
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

  onMount(load);
</script>

<section class="row-section">
  <header class="head">
    <h2>For you</h2>
    <a href="/for-you" class="more">See all →</a>
  </header>

  {#if loading}
    <div class="placeholder">Tuning your recommendations…</div>
  {:else if items.length === 0}
    <div class="placeholder empty">
      No recommendations yet — try a few searches and favourites so the
      ranker has signal.
    </div>
  {:else}
    <div class="scroller" role="list">
      {#each items as it, i (it.id + ':' + i)}
        <div class="cell">
          <PhotoTile
            pointId={it.id}
            blurhash={it.blurhash ?? null}
            scoreStr={it.score_str ?? ''}
            isFavorite={it.is_favorite ?? false}
            onOpen={() => (lightboxIndex = i)}
          />
        </div>
      {/each}
    </div>
  {/if}
</section>

{#if lightboxIndex !== null && items.length}
  <Lightbox
    items={items.map((i) => ({ id: i.id, blurhash: i.blurhash ?? null, isFavorite: !!i.is_favorite }))}
    index={lightboxIndex}
    onClose={() => (lightboxIndex = null)}
  />
{/if}

<style>
  .row-section {
    margin-top: var(--s-5);
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
