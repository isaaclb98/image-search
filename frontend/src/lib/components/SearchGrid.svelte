<script lang="ts">
  /**
   * SearchGrid — virtualized grid of photo tiles.
   * Used by Search, Random, For-You, Home, Albums, Similar.
   *
   *   - Renders tiles via PhotoTile
   *   - Right-click on a tile opens the ImageContextMenu
   *   - Left-click opens the Lightbox
   *   - Infinite scroll: when the sentinel near the bottom
   *     intersects the viewport, calls onLoadMore
   *   - Virtual scrolling: only renders visible rows for perf
   */
  import { onMount, onDestroy } from 'svelte';
  import { pageTint } from '$lib/stores/tint';
  import { photoUrl } from '$lib/api/endpoints';
  import { createWindowVirtualizer } from '@tanstack/svelte-virtual';
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
    is_disliked?: boolean;
  };

  type Props = {
    items: Item[];
    loading?: boolean;
    hasMore?: boolean;
    onLoadMore?: () => void;
    onToggleFavorite?: (id: string) => void;
    onDislike?: (id: string) => void;
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

  // Grid config
  const COLUMNS = 5;
  const GAP = 12; // px, matches --s-2
  const ESTIMATED_ROW_HEIGHT = 280; // px, approximate tile height + gap

  // State
  let lightboxIndex = $state<number | null>(null);
  let contextMenu = $state<{ x: number; y: number; item: Item } | null>(null);
  // Grid wrapper ref (for width measurement only — not a scroll parent).
  // The body is the scroll context; the virtualizer watches window.
  let gridWrapper: HTMLDivElement | undefined = $state();
  let containerWidth = $state(0);

  // Calculate tile size based on container width
  let tileSize = $derived(
    containerWidth > 0
      ? (containerWidth - (COLUMNS - 1) * GAP) / COLUMNS
      : ESTIMATED_ROW_HEIGHT
  );
  let rowHeight = $derived(tileSize + GAP);

  // Group items into rows
  let rows = $derived(
    Array.from({ length: Math.ceil(items.length / COLUMNS) }, (_, i) =>
      items.slice(i * COLUMNS, (i + 1) * COLUMNS)
    )
  );

  // Virtualizer — created once, scrolls with window. Created at
  // module scope so it isn't recreated on every `rows` change
  // (recreating resets scroll position and drops the new items).
  const virtualizerStore = createWindowVirtualizer({
    count: 0,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    overscan: 5
  });

  // Stable reference to the virtualizer instance. Don't use $derived
  // here — setOptions forces a store update, which would re-trigger
  // the $effect below and call setOptions again, creating a loop.
  let theVirtualizer: ReturnType<typeof virtualizerStore.subscribe> extends (cb: (v: infer T) => any) => any ? T : never;
  virtualizerStore.subscribe(v => { theVirtualizer = v; });

  // Push reactive count/rowHeight into the virtualizer when they change.
  // Note: do NOT read `theVirtualizer` here — only rows.length and
  // rowHeight are the reactive dependencies.
  $effect(() => {
    const n = rows.length;
    const h = rowHeight;
    theVirtualizer?.setOptions({
      count: n,
      estimateSize: () => h
    });
  });

  // Read virtualItems/totalSize from the store (re-runs when store updates).
  let virtualItems = $derived($virtualizerStore?.getVirtualItems() ?? []);
  let totalSize = $derived($virtualizerStore?.getTotalSize() ?? 0);

  // ResizeObserver for container width
  let resizeObserver: ResizeObserver | null = null;

  onMount(() => {
    if (gridWrapper) {
      resizeObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          containerWidth = entry.contentRect.width;
        }
      });
      resizeObserver.observe(gridWrapper);
    }
  });

  onDestroy(() => {
    resizeObserver?.disconnect();
  });

  // Infinite scroll: sentinel. root: null → viewport (window).
  let sentinel: HTMLDivElement | undefined = $state();
  let observer: IntersectionObserver | null = null;

  $effect(() => {
    if (sentinel && hasMore && onLoadMore && !loading) {
      observer?.disconnect();
      observer = new IntersectionObserver(
        (entries) => {
          if (entries[0].isIntersecting) {
            onLoadMore?.();
          }
        },
        { root: null, threshold: 0.1 }
      );
      observer.observe(sentinel);
    }
    return () => observer?.disconnect();
  });

  // Tile interactions
  function openLightbox(itemIndex: number) {
    lightboxIndex = itemIndex;
  }

  function closeLightbox() {
    lightboxIndex = null;
  }

  function openContextMenu(item: Item, e: MouseEvent) {
    e.preventDefault();
    contextMenu = { x: e.clientX, y: e.clientY, item };
  }

  function closeContextMenu() {
    contextMenu = null;
  }
</script>

{#if loading && items.length === 0}
  <div class="empty loading">
    <div class="spinner" aria-label="Loading results"></div>
    <span>Searching...</span>
  </div>
{:else if items.length === 0 && !loading}
  <div class="empty">No results</div>
{:else}
  <div class="grid-wrapper" bind:this={gridWrapper}>
    <div
      class="grid-virtual"
      style="height: {totalSize}px; position: relative;"
    >
      {#each virtualItems as virtualRow (virtualRow.key)}
        <div
          class="grid-row"
          style="position: absolute; top: 0; left: 0; width: 100%; height: {virtualRow.size}px; transform: translateY({virtualRow.start}px);"
        >
          {#each rows[virtualRow.index] as item, colIndex}
            {@const itemIndex = virtualRow.index * COLUMNS + colIndex}
            <div class="grid-tile">
              <PhotoTile
                pointId={item.id}
                blurhash={item.blurhash}
                scoreStr={item.score_str}
                isFavorite={item.is_favorite}
                isDisliked={item.is_disliked}
                onOpen={() => openLightbox(itemIndex)}
                onContextMenu={(id, e) => openContextMenu(item, e)}
              />
            </div>
          {/each}
        </div>
      {/each}
    </div>

    {#if hasMore}
      <div class="sentinel" bind:this={sentinel}>
        {#if loading}
          <div class="loading">Loading...</div>
        {/if}
      </div>
    {/if}
  </div>
{/if}

{#if lightboxIndex !== null && lightboxIndex < items.length}
  <Lightbox
    items={items.map((it) => ({
      id: it.id,
      blurhash: it.blurhash,
      isFavorite: it.is_favorite,
      isDisliked: it.is_disliked
    }))}
    index={lightboxIndex}
    onClose={closeLightbox}
    onToggleFavorite={onToggleFavorite}
    onDislike={onDislike}
    albums={albums}
  />
{/if}

{#if contextMenu}
  <ImageContextMenu
    x={contextMenu.x}
    y={contextMenu.y}
    pointId={contextMenu.item.id}
    path={contextMenu.item.path}
    isFavorite={contextMenu.item.is_favorite}
    albums={albums}
    onClose={closeContextMenu}
    onToggleFavorite={onToggleFavorite}
  />
{/if}

<style>
  .grid-wrapper {
    /* Body is the scroll context. The grid is in normal flow; only
       virtual rows the user is looking at are rendered. */
    width: 100%;
  }

  .grid-virtual {
    width: 100%;
  }

  .grid-row {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: var(--s-2, 12px);
    padding: 0 var(--s-4, 24px);
    box-sizing: border-box;
  }

  .grid-tile {
    aspect-ratio: 1;
    min-width: 0;
  }

  .empty {
    padding: var(--s-6, 48px) var(--s-4, 24px);
    color: var(--fg-3, #7e8290);
    font-size: 0.95rem;
  }

  .sentinel {
    padding: var(--s-4, 24px);
    text-align: center;
  }

  .loading {
    color: var(--fg-3, #7e8290);
    font-size: 0.9rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--s-3, 12px);
  }

  .spinner {
    width: 32px;
    height: 32px;
    border: 3px solid var(--glass-edge, rgba(255, 255, 255, 0.1));
    border-top-color: var(--accent, #6cc6ff);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
