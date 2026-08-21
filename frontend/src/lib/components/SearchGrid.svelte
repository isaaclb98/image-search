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
  import { pageTint } from '$lib/stores/tint';
  import { photoUrl } from '$lib/api/endpoints';
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
    onDislike?: (id: string) => void;
    /** User-created albums for the right-click "Add to album" submenu.
     *  When omitted, the menu fetches them lazily on first hover. */
    albums?: { id: number; name: string }[];
  };
  let {
    items,
    loading = false,
    hasMore = false,
    onLoadMore,
    onToggleFavorite,
    onDislike,
    albums
  }: Props = $props();

  $effect(() => {
    if (items.length > 0 && items[0].blurhash) {
      // Push the first photo's URL to drive the full-viewport backdrop.
      // Heavily blurred + saturated in CSS, so the room tint comes from
      // the actual photo, not a tiny blurhash data URL.
      const first = items[0];
      if (first?.id) pageTint.set(photoUrl(first.id));
    }
  });

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
  //
  // We gate loadMore on window.scrollY > 50 to avoid a one-shot
  // fire on initial render where the IO sees the sentinel as
  // intersecting right at mount (the prior round had this gate
  // at the IO level but with a too-large rootMargin that left
  // the sentinel "intersecting" for the whole page, so the gate
  // alone didn't help). Now the rootMargin is small enough that
  // the sentinel only intersects as the user scrolls near the
  // bottom — and the scrollY gate is a belt-and-suspenders check.
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
          if (
            ent.isIntersecting &&
            hasMore &&
            !loading &&
            window.scrollY > 50
          ) {
            onLoadMore();
          }
        }
      },
      // Sentinel must actually be near the bottom of the viewport
      // for it to count as intersecting. With a larger margin the
      // sentinel was always "intersecting" on a page taller than
      // the viewport, so the IO never re-fired when the user
      // actually scrolled.
      { rootMargin: '0px 0px 200px 0px' }
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
    onDislike={(id) => onDislike?.(id)}
  />
{/if}

{#if menuFor}
  <ImageContextMenu
    pointId={menuFor.id}
    path={menuFor.path}
    isFavorite={menuFor.isFavorite}
    x={menuFor.x}
    y={menuFor.y}
    {albums}
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
