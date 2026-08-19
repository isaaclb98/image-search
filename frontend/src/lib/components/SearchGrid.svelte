<script lang="ts">
  /**
   * SearchGrid — the canonical 5-column grid of photo tiles,
   * used by Search, Random, For-You, Home, Albums (and
   * anywhere else with a list of points to display).
   *
   *   - Renders tiles via PhotoTile
   *   - Right-click on a tile opens the ImageContextMenu
   *   - Left-click opens the Lightbox
   *   - Infinite scroll: when the sentinel near the bottom
   *     intersects the viewport, calls onLoadMore
   *   - Empty / loading / error states are explicit
   */
  import { onMount } from 'svelte';
  import PhotoTile from './PhotoTile.svelte';
  import ImageContextMenu from './ImageContextMenu.svelte';
  import Lightbox from './Lightbox.svelte';

  type Item = {
    id: string;
    path?: string;
    score?: number;
    score_str?: string;
    blurhash?: string | null;
    is_favorite?: boolean;
  };

  type Props = {
    items: Item[];
    /** True while more pages are being fetched. */
    loading?: boolean;
    /** True when there are no more pages to load. */
    hasMore?: boolean;
    onLoadMore?: () => void;
    onToggleFavorite?: (id: string) => void;
  };
  let {
    items,
    loading = false,
    hasMore = false,
    onLoadMore,
    onToggleFavorite
  }: Props = $props();

  let sentinel: HTMLDivElement | undefined = $state();
  let lightboxIndex = $state<number | null>(null);
  let menuFor = $state<{ id: string; x: number; y: number; path?: string; isFavorite?: boolean } | null>(null);

  function openAt(i: number) {
    lightboxIndex = i;
  }
  function openContext(id: string, e: MouseEvent) {
    const it = items.find((x) => x.id === id);
    menuFor = {
      id,
      x: e.clientX,
      y: e.clientY,
      path: it?.path,
      isFavorite: it?.is_favorite
    };
  }
  function closeMenu() {
    menuFor = null;
  }

  // IntersectionObserver for infinite scroll. The sentinel div
  // may not exist at onMount time (the grid is initially empty
  // while items are being fetched), so we use `$effect` to set up
  // the observer whenever the sentinel becomes available. Re-running
  // the effect is fine — each call disconnects the previous IO.
  let io: IntersectionObserver | undefined;
  $effect(() => {
    // Touch the reactive deps so the effect re-runs on changes.
    void sentinel;
    void hasMore;
    void loading;
    if (!sentinel || !onLoadMore) return;
    if (!hasMore) {
      io?.disconnect();
      return;
    }
    io?.disconnect();
    io = new IntersectionObserver(
      (entries) => {
        for (const ent of entries) {
          if (ent.isIntersecting && hasMore && !loading) {
            onLoadMore();
          }
        }
      },
      { rootMargin: '600px 0px' }
    );
    io.observe(sentinel);
    return () => io?.disconnect();
  });
</script>

{#if items.length === 0 && !loading}
  <div class="empty">
    <div class="empty-mark" aria-hidden="true"></div>
    <p>No photos yet. Try a search or browse Random.</p>
  </div>
{:else}
  <div class="grid" role="list">
    {#each items as it, i (it.id + ':' + i)}
      <PhotoTile
        pointId={it.id}
        blurhash={it.blurhash ?? null}
        scoreStr={it.score_str ?? ''}
        isFavorite={it.is_favorite ?? false}
        contextMenuOpen={menuFor?.id === it.id}
        onOpen={() => openAt(i)}
        onContextMenu={openContext}
      />
    {/each}
  </div>
  {#if hasMore}
    <div bind:this={sentinel} class="sentinel" aria-hidden="true"></div>
    {#if loading}
      <div class="loading">Loading more…</div>
    {/if}
  {:else if items.length > 0}
    <div class="loading end">End of results.</div>
  {/if}
{/if}

{#if lightboxIndex !== null && items.length}
  <Lightbox
    items={items.map((i) => ({
      id: i.id,
      blurhash: i.blurhash ?? null,
      isFavorite: i.is_favorite ?? false
    }))}
    index={lightboxIndex}
    onClose={() => (lightboxIndex = null)}
    onToggleFavorite={(id) => onToggleFavorite?.(id)}
  />
{/if}

{#if menuFor}
  <ImageContextMenu
    pointId={menuFor.id}
    path={menuFor.path}
    isFavorite={menuFor.isFavorite}
    x={menuFor.x}
    y={menuFor.y}
    onClose={closeMenu}
    onToggleFavorite={(id) => { onToggleFavorite?.(id); closeMenu(); }}
  />
{/if}

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: var(--grid-gutter);
  }
  @media (max-width: 1400px) {
    .grid { grid-template-columns: repeat(4, 1fr); }
  }
  @media (max-width: 1000px) {
    .grid { grid-template-columns: repeat(3, 1fr); }
  }
  @media (max-width: 720px)  {
    .grid { grid-template-columns: repeat(2, 1fr); }
  }
  .sentinel { height: 1px; }
  .loading {
    color: var(--fg-3);
    text-align: center;
    padding: 32px 0;
    font-size: var(--fs-sm);
  }
  .loading.end { color: var(--fg-2); }
  .empty {
    text-align: center;
    padding: 80px 24px;
    color: var(--fg-2);
  }
  .empty-mark {
    width: 64px; height: 64px;
    margin: 0 auto 16px;
    border-radius: 50%;
    background:
      radial-gradient(circle at 30% 30%, var(--accent-soft), transparent 60%),
      radial-gradient(circle at 70% 70%, rgba(255,122,138,0.18), transparent 60%),
      var(--glass-1);
    border: 1px solid var(--glass-edge);
  }
</style>
