<script lang="ts">
  /**
   * PhotoGrid — the single, canonical photo-tile grid.
   *
   * Round‑27: unified the old SearchGrid + ForYouRow into one
   * component. Used by every page that lays out photo tiles:
   *   - Home page search results
   *   - Home page "For you" row (via ForYouRow wrapper)
   *   - /random, /for-you, /search, /similar/[id], /albums/[id]
   *
   *   - Renders tiles via PhotoTile
   *   - Right-click on a tile opens the ImageContextMenu
   *   - Left-click opens the Lightbox (or runs onPhotoOpen when set)
   *   - Infinite scroll: when the sentinel near the bottom
   *     intersects the viewport, calls onLoadMore
   *   - Virtual scrolling: only renders visible rows for perf;
   *     no‑op when the item count fits in one viewport (the common
   *     case for the Home For-You row of 20 items).
   *
   * All callers share one outer padding token and one gutter token
   * (--s-4 and --grid-gutter), so the grid renders the same on every
   * page. No more divergent copy-paste.
   */
  import { onMount, onDestroy } from 'svelte';
  import { pageTint } from '$lib/stores/tint';
  import { photoUrl, thumbUrl } from '$lib/api/endpoints';
  import type { SearchResult } from '$lib/api/endpoints';
  import { blurhashToDataUrl } from '$lib/components/blurhash-bg';
  import { createWindowVirtualizer } from '@tanstack/svelte-virtual';
  import type { SvelteVirtualizer } from '@tanstack/svelte-virtual';
  import PhotoTile from './PhotoTile.svelte';
  import ImageContextMenu from './ImageContextMenu.svelte';
  import Lightbox from './Lightbox.svelte';

  // Use the generated SearchResult shape so `url`, `width`,
  // `height`, `is_disliked` etc. stay in lockstep with the
  // backend. Everything optional because some callers (ForYouRow)
  // pass a strict subset and that's fine.
  type Item = Partial<SearchResult> & { id: string };

  type Props = {
    items: Item[];
    loading?: boolean;
    hasMore?: boolean;
    onLoadMore?: () => void;
    onToggleFavorite?: (id: string) => void;
    onDislike?: (id: string) => void;
    /**
     * Optional remove affordance shown as a small − button in the
     * top-right corner of every tile (revealed on hover, or always
     * on touch). Forwarded to PhotoTile. See PhotoTile's docs for
     * the rationale (curating sets: likes, dislikes, albums).
     */
    onRemove?: (id: string) => void;
    /** Tooltip / aria-label for the remove button. */
    removeLabel?: string;
    albums?: { id: number; name: string }[];
    /**
     * Optional override for the default left-click behaviour
     * (open the lightbox). The Similar page uses this to take the
     * user to the dedicated photo page instead of opening a
     * lightbox on top of the dedicated page. The handler receives
     * the clicked item so the caller can decide what to do.
     */
    onPhotoOpen?: (item: Item) => void;
    /**
     * Bindable. When the parent owns the lightbox state (e.g. the
     * ForYouRow wrapper), bind here so clicks reset the parent's
     * lightboxIndex instead of an internal one. If unbound, the
     * component manages its own lightboxIndex internally.
     */
    lightboxIndex?: number | null;
  };

  let {
    items,
    loading = false,
    hasMore = false,
    onLoadMore,
    onToggleFavorite,
    onDislike,
    onRemove,
    removeLabel,
    albums,
    onPhotoOpen,
    lightboxIndex = $bindable<number | null>(null)
  }: Props = $props();

  // Grid config
  //
  // GAP is the row-to-row vertical spacing used by the virtualizer
  // (rowHeight = tileSize + GAP). It MUST match the CSS `gap` on
  // `.grid-row` (--grid-gutter) so horizontal and vertical gutters
  // read as the same thickness — otherwise tiles look balanced
  // side-to-side but cramped top-to-bottom.
  //
  // Round‑36: tightened from 20 to 4 so the grid reads as a dense
  // wall of photos instead of an airy card layout. ESTIMATED_ROW_HEIGHT
  // stays at 280 since it was already larger than tileSize + GAP — the
  // estimate just gets more accurate.
  const GAP = 4; // px, matches --grid-gutter
  const ESTIMATED_ROW_HEIGHT = 280; // px, approximate tile height + gap

  // Sensible floor before we know the wrapper width — used on the
  // very first render when both containerWidth and tileSize are 0.
  // Round‑36: bumped from 5 to 6 — at the 2400px container cap the
  // new fixed 384px tiles give 6 cols. The ResizeObserver fires
  // within one frame and corrects to the real value before the
  // user notices.
  const ESTIMATED_COLUMNS = 6;

  // State
  let contextMenu = $state<{ x: number; y: number; item: Item } | null>(null);
  // Grid wrapper ref (for width measurement only — not a scroll parent).
  // The body is the scroll context; the virtualizer watches window.
  let gridWrapper: HTMLDivElement | undefined = $state();
  let containerWidth = $state(0);
  // Round‑32: actual rendered tile width (read from DOM). The CSS
  // grid uses auto-fill so the number of columns changes with
  // viewport width — we measure the first tile directly. Default
  // to the previous JS estimate so the virtualizer has a sane
  // starting value before the first measurement lands.
  let renderedTileSize = $state(0);

  // tileSize drives the virtualizer's rowHeight (= tileSize + GAP).
  // The CSS grid decides the actual tile width (auto-fill means it
  // changes with viewport), so we read it directly off a rendered
  // `.grid-tile`. Until the first tile mounts we fall back to the
  // ESTIMATED_ROW_HEIGHT — the ResizeObserver fires within one
  // frame and the $effect below re-measures.
  let tileSize = $derived(renderedTileSize > 0 ? renderedTileSize : ESTIMATED_ROW_HEIGHT);
  let rowHeight = $derived(tileSize + GAP);

  // Derive column count from the same math the CSS uses:
  //   containerWidth = COLUMNS * tileSize + (COLUMNS - 1) * GAP
  // Solving for COLUMNS:
  //   COLUMNS = (containerWidth + GAP) / (tileSize + GAP)
  // Falls back to ESTIMATED_COLUMNS on the first frame (before any
  // measurement). Floors to 1 if the wrapper is narrower than a
  // single min-width tile. Kept in sync with `.grid-row`'s
  // `auto-fill minmax(COLUMN_MIN, 1fr)` so the virtualizer's row
  // slicing matches the DOM's actual column count — otherwise the
  // last columns of every row stay empty.
  let columns = $derived(
    tileSize > 0 && containerWidth > 0
      ? Math.max(1, Math.floor((containerWidth + GAP) / (tileSize + GAP)))
      : ESTIMATED_COLUMNS
  );

  // Group items into rows
  let rows = $derived(
    Array.from({ length: Math.ceil(items.length / columns) }, (_, i) =>
      items.slice(i * columns, (i + 1) * columns)
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
  let theVirtualizer: SvelteVirtualizer<Window, Element> | undefined;
  virtualizerStore.subscribe((v) => {
    theVirtualizer = v;
  });

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
    // setOptions updates the estimate but doesn't always force the
    // virtualizer to recompute cached row offsets. Call measure()
    // explicitly so already-laid-out rows pick up the new rowHeight
    // — without this, the first paint uses the initial fallback
    // (280 + GAP) and rows visually touch each other on first load.
    theVirtualizer?.measure?.();
  });

  // Read virtualItems/totalSize from the store (re-runs when store updates).
  let virtualItems = $derived($virtualizerStore?.getVirtualItems() ?? []);
  let totalSize = $derived($virtualizerStore?.getTotalSize() ?? 0);

  // ResizeObserver for container width.
  //
  // Use $effect instead of onMount: in Svelte 5, bind:this on a
  // {#if}-gated element only populates the variable once the branch
  // renders, which happens AFTER onMount when items load async.
  // The $effect re-runs whenever gridWrapper flips undefined → bound.
  //
  // The second $effect (further down) re-measures the rendered tile
  // whenever items.length changes — the ResizeObserver fires for
  // the wrapper, but `.grid-tile` elements only exist once items
  // load, so we read the tile width on every items change too.
  let resizeObserver: ResizeObserver | null = null;

  $effect(() => {
    if (!gridWrapper) return;
    // Sync current width now that the wrapper is mounted.
    containerWidth = gridWrapper.getBoundingClientRect().width;
    if (resizeObserver) return;
    resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        containerWidth = entry.contentRect.width;
      }
    });
    resizeObserver.observe(gridWrapper);
  });

  // Re-measure the rendered tile whenever items change OR the
  // container width changes. The CSS grid uses auto-fill, so the
  // number of columns (and therefore the tile width) depends on
  // `containerWidth`. The ResizeObserver may fire before the
  // first row renders, so we read the tile on every items change
  // too.
  $effect(() => {
    // Track dependencies.
    void items.length;
    void containerWidth;
    if (!gridWrapper) return;
    // Defer one tick so the DOM has rendered the new tiles.
    queueMicrotask(() => {
      const firstTile = gridWrapper!.querySelector('.grid-tile');
      if (firstTile) {
        const r = firstTile.getBoundingClientRect();
        if (r.width > 0) renderedTileSize = r.width;
      }
    });
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

  // Round‑31: push the most-recently-in-view tile's blurhash to
  // the pageTint store. This gives every grid page (/, /random,
  // /for-you, /albums/likes, /albums/dislikes, /similar/…) a
  // colour wash even when no lightbox is open.
  //
  // The lightbox effect below overrides this with the full
  // /photo/{id}/raw URL when a lightbox is open, so the backdrop
  // shows the actual photo (heavily blurred) rather than a flat
  // blurhash tint during interactive viewing.
  //
  // Implementation: debounce so we don't fire blurhashToDataUrl
  // on every scroll. The output is a 64×40 PNG data URL — small
  // enough to set on every "settled" position without cost.
  $effect(() => {
    // Track `virtualItems` so this re-runs when the row scrolls.
    const vis = virtualItems;
    if (vis.length === 0 || items.length === 0) return;
    if (lightboxIndex !== null) return; // lightbox effect owns the tint
    // First visible item — the anchor for the current view.
    const firstVisRow = vis[0];
    const topItem = items[firstVisRow.index];
    const hash = topItem?.blurhash;
    if (!hash) return;
    // Debounce so we don't churn on every scroll frame.
    let cancelled = false;
    const id = setTimeout(() => {
      blurhashToDataUrl(hash, 64, 40).then((url) => {
        if (!cancelled && url) pageTint.set(url);
      }).catch(() => {
        // blurhash decode can throw on malformed hashes; safe to ignore.
      });
    }, 80);
    return () => {
      cancelled = true;
      clearTimeout(id);
    };
  });

  // Round‑31 fix: actually push the active photo's URL to the
  // global pageTint store so +layout.svelte can paint a colour
  // bleed behind the page. Previously the store was imported but
  // never written to, so the backdrop stayed solid black.
  //
  // On lightbox open: set the URL of the active item.
  // On lightbox close: the grid‑tint effect above re‑engages
  // (it sees lightboxIndex === null and pushes a blurhash), so
  // no separate clear is needed — that was the source of a
  // race where the clear timer fired AFTER the grid tint and
  // wiped it. Pages that use PhotoGrid with no items fall
  // through to the default dark backdrop.
  //
  // Round‑10: use the thumbnail URL instead of the raw
  // photo URL. The backdrop is rendered at 100vw × 100vh
  // and blurred 60 px in CSS — a 384 px source upscaled
  // and blurred is perceptually identical to the full-res
  // version for ambient atmosphere, but cuts the transfer
  // size from a typical 3-5 MB JPEG down to a ~20 KB
  // WebP. Big win on the user-perceived lightbox open
  // latency. Post the model-variant migration plan, 384
  // is the single thumbnail size served by the indexer
  // (matches the so400m model input resolution).
  $effect(() => {
    const i = lightboxIndex;
    if (i !== null && i >= 0 && i < items.length) {
      const it = items[i];
      if (it?.id) {
        pageTint.set(thumbUrl(it.id, 384));
      }
    }
  });

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
      {#each virtualItems as virtualRow, vRowIdx (virtualRow.key)}
        <div
          class="grid-row"
          style="position: absolute; top: 0; left: 0; width: 100%; height: {virtualRow.size}px; transform: translateY({virtualRow.start}px);"
        >
          {#each rows[virtualRow.index] as item, colIndex}
            {@const itemIndex = virtualRow.index * columns + colIndex}
            <!-- Tier 1 of 3 (perf round 1): tag the first three tiles
                 of the first visible row as eager so the browser
                 starts fetching them with the rest of the HTML. The
                 virtualizer guarantees this row is in the viewport
                 (overscan ≥ 5 covers the first row even when scrolled). -->
            {@const eagerIndex = vRowIdx === 0 ? colIndex : null}
            <div class="grid-tile">
              <PhotoTile
                pointId={item.id}
                blurhash={item.blurhash}
                scoreStr={item.score_str}
                isFavorite={item.is_favorite}
                isDisliked={item.is_disliked}
                {eagerIndex}
                onOpen={onPhotoOpen ? () => onPhotoOpen(item) : () => openLightbox(itemIndex)}
                onContextMenu={(id, e) => openContextMenu(item, e)}
                onRemove={onRemove}
                removeLabel={removeLabel}
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
       virtual rows the user are looking at are rendered. */
    width: 100%;
  }

  .grid-virtual {
    width: 100%;
  }

  .grid-row {
    display: grid;
    /* Round‑36: fixed 384px tiles (was auto-fill minmax(180, 1fr)
       → 8 cols at ~183px). Fixed 384 matches the thumbnail source
       1:1 — no upscale, no downscale. auto-fill + fixed track size
       packs as many 384px cols as fit; leftover space (e.g. 1392px
       container → 3 cols of 384 with 232px slack) stays empty
       rather than stretching tiles. Round‑36 container cap is
       2400px, so 6 cols fit cleanly at the 2352px wrapper width. */
    grid-template-columns: repeat(auto-fill, 384px);
    /* Round‑36: tightened from 20px to 4px. A dense wall-of-photos
       reads as a real gallery; the old 20px gap looked like a
       card grid. Keep this in sync with the JS-side `GAP`
       constant above so virtualizer row-height math is exact. */
    gap: var(--grid-gutter, 4px);
    /* No horizontal padding here — `.app-main` already provides
       24px of side padding, and the row belongs to the wrapper
       which fills main's content area. Adding more here would
       double-inset the grid (48px total side padding) and make
       it look left-shifted on wide viewports where the wrapper
       is narrower than the viewport. */
  }

  .grid-tile {
    aspect-ratio: 1;
    min-width: 0;
    /* Round‑36: CSS containment isolates each tile's size, layout,
       paint, and style from the rest of the grid. With a wall
       of 100+ tiles on screen, this prevents the browser from
       re-laying-out the whole grid when one tile's contents
       change (e.g. blurhash decoded, hover chrome mounted).
       No visual cost; small paint-side memory bump. */
    contain: size layout paint style;
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

  /* No dim-on-hover effect (Round‑36 removed).
     The row-level dim felt like a UI interruption on the dense
     6×4K grid — non-hovered tiles already have their own hover
     state (scale + border), so dimming siblings added noise
     without conveying useful info. */
</style>
