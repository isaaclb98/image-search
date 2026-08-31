<script lang="ts">
  /**
   * One tile in the grid. Renders:
   *   - blurhash as a tinted placeholder (decoded async, cheap)
   *   - the real photo on top, once loaded
   *   - a glass "hover" chrome with the score (top-left) and
   *     context-menu trigger (top-right). No buttons until hover.
   *   - a persistent visual state for liked (pink glow + badge)
   *     and disliked (cool-grey dim + slash) tiles so the user
   *     can see their feedback across all pages. (Round-6
   *     issue #3 — Like/Dislike should change colour across
   *     the board, not just in the lightbox.)
   *
   * Tiles are responsible for their own click + right-click
   * interactions. The grid above just lays them out.
   */
  import { onMount } from 'svelte';
  import { thumbUrl } from '../api/endpoints';
  import { blurhashToDataUrl } from './blurhash-bg';

  type Props = {
    pointId: string;
    blurhash?: string | null;
    /** Back-compat alias for `blurhash` — ForYouRow passes the
     *  pre-computed blurhash data URL under this name. */
    dataUrl?: string | null;
    scoreStr?: string;
    isFavorite?: boolean;
    isDisliked?: boolean;
    contextMenuOpen?: boolean;
    /**
     * Tier 1 of 3 (perf round 1): first-screen tiles should bypass
     * the browser's lazy-loader so the initial grid paint isn't gated
     * on the lazy-load heuristic. 0-indexed position in the visible
     * items array; false/null → normal lazy behaviour. We pass this
     * down from PhotoGrid because PhotoTile doesn't know whether it's
     * in the first row of the visible virtualizer slice.
     */
    eagerIndex?: number | false | null;
    onOpen?: (id: string) => void;
    onContextMenu?: (id: string, e: MouseEvent) => void;
    /**
     * Optional remove affordance. When provided, a small circular
     * button appears at the top-right of the tile (replacing the
     * corner spot the persistent fav/dislike badges use, so the
     * remove badge takes precedence visually when both apply).
     * Used by the album detail page, the Likes grid, and the
     * Dislikes grid — i.e. anywhere the user is curating a set of
     * photos and removing one is the primary action.
     *
     * The remove button is invisible by default and fades in on
     * hover/focus so it doesn't visually clutter the tile grid;
     * on touch / coarse-pointer devices where there's no hover, it
     * stays visible (the typical mobile pattern: action chips are
     * always present). Uses data-no-open so the click doesn't open
     * the photo lightbox.
     */
    onRemove?: (id: string) => void;
    /** Visible label on the remove button + its title attribute. */
    removeLabel?: string;
  };
  let {
    pointId,
    blurhash,
    dataUrl: dataUrlProp,
    scoreStr,
    isFavorite,
    isDisliked,
    contextMenuOpen,
    eagerIndex,
    onOpen,
    onContextMenu,
    onRemove,
    removeLabel = 'Remove'
  }: Props = $props();

  // Pre-computed data URL takes priority (caller already decoded
  // the blurhash), otherwise we resolve it lazily on mount.
  let dataUrl = $state<string | null>(dataUrlProp ?? null);
  let loaded = $state(false);
  let imgEl: HTMLImageElement | undefined = $state();

  onMount(() => {
    if (!dataUrl && blurhash) {
      blurhashToDataUrl(blurhash).then((u) => {
        if (u) dataUrl = u;
      });
    }
  });

  function handleClick(e: MouseEvent) {
    if ((e.target as HTMLElement).closest('[data-no-open]')) return;
    e.preventDefault();
    onOpen?.(pointId);
  }
  function handleContext(e: MouseEvent) {
    e.preventDefault();
    onContextMenu?.(pointId, e);
  }
  // Best-effort tile-width hint for the first-screen srcset. We can't
  // measure the tile itself from here (no DOM ref on the wrapping
  // grid cell), so we approximate from window.innerWidth divided by
  // the auto-fill column target. Falls back to 240 on SSR / dev
  // edge cases. Cached per-tile via $derived so we only recompute
  // when the breakpoint flips.
  function tileSizeGuess(): number | undefined {
    if (typeof window === 'undefined') return undefined;
    const w = window.innerWidth;
    // Mirror PhotoGrid's `auto-fill minmax(180px, 1fr)` heuristic.
    const cols = Math.max(1, Math.floor((Math.min(w, 1600) + 20) / (180 + 20)));
    return Math.floor((Math.min(w, 1600) - (cols - 1) * 20) / cols);
  }
  // True for the first three tiles of the first visible row. These
  // are the tiles a user is staring at on first paint — we want them
  // marked eager + high-priority so the browser starts fetching them
  // alongside the HTML/CSS instead of waiting on its lazy heuristic.
  let isEager = $derived(eagerIndex === 0 || eagerIndex === 1 || eagerIndex === 2);
  // Rendered srcset width for the eager tiles. We round up the
  // guessed tile size × 2 so the picked variant is at least 2× the
  // CSS pixels (retina). 480 is the upper bound — anything bigger
  // would be the canonical 256-px file upscaled and we already serve
  // 480 from the backend.
  let eagerSrcWidth = $derived(
    Math.max(120, Math.min(240, Math.round((tileSizeGuess() ?? 240) * 1)))
  );
  let eagerSrcset = $derived(
    `${thumbUrl(pointId, 120)} 120w, ${thumbUrl(pointId, 180)} 180w, ${thumbUrl(pointId, 240)} 240w`
  );
  let eagerSizes = $derived(
    '(max-width: 600px) 120px, (max-width: 1200px) 180px, 240px'
  );
</script>

<a
  class="tile"
  class:loaded
  class:favorite={isFavorite}
  class:disliked={isDisliked}
  class:menu-open={contextMenuOpen}
  href={`/photo/${encodeURIComponent(pointId)}`}
  onclick={handleClick}
  oncontextmenu={handleContext}
  aria-label="Open photo"
>
  {#if dataUrl}
    <img class="ph" src={dataUrl} alt="" aria-hidden="true" />
  {:else}
    <div class="ph fallback" aria-hidden="true"></div>
  {/if}
  <img
    bind:this={imgEl}
    class="full"
    src={isEager ? thumbUrl(pointId, eagerSrcWidth) : thumbUrl(pointId)}
    srcset={isEager ? eagerSrcset : undefined}
    sizes={isEager ? eagerSizes : undefined}
    alt=""
    loading={isEager ? 'eager' : 'lazy'}
    fetchpriority={eagerIndex === 0 ? 'high' : isEager ? 'auto' : 'low'}
    decoding="async"
    onload={() => (loaded = true)}
    onerror={() => (loaded = true)}
  />
  {#if scoreStr}
    <span class="score">{scoreStr}</span>
  {/if}
  {#if isFavorite}
    <span class="fav" data-no-open title="Like">♥</span>
  {/if}
  {#if isDisliked}
    <span class="neg-badge" data-no-open title="Dislike">−</span>
  {/if}
  {#if onRemove}
    <button
      type="button"
      class="remove-btn"
      data-no-open
      title={removeLabel}
      aria-label={removeLabel}
      onclick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onRemove?.(pointId);
      }}
    >
      −
    </button>
  {/if}
</a>

<style>
  .tile {
    position: relative;
    display: block;
    aspect-ratio: 1 / 1;
    border-radius: var(--r-2);
    overflow: hidden;
    background: var(--bg-1);
    border: 1px solid var(--glass-edge);
    transition: transform var(--t-fast) var(--ease-out),
                border-color var(--t-fast) var(--ease-out),
                box-shadow var(--t-fast) var(--ease-out),
                opacity var(--t-fast) var(--ease-out);
  }
  .tile:hover {
    transform: translateY(-2px);
    border-color: var(--glass-edge-strong);
    box-shadow: var(--shadow-2);
  }
  .tile.menu-open {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent), var(--shadow-2);
  }
  /* Liked — pink accent border + subtle outer glow so the user
     can see the photo is saved without hovering. */
  .tile.favorite {
    border-color: rgba(255, 122, 138, 0.55);
    box-shadow: 0 0 0 1px rgba(255, 122, 138, 0.35),
                0 0 14px rgba(255, 122, 138, 0.18);
  }
  .tile.favorite:hover {
    border-color: rgba(255, 122, 138, 0.85);
    box-shadow: 0 0 0 1px rgba(255, 122, 138, 0.55),
                0 0 18px rgba(255, 122, 138, 0.28);
  }
  /* Disliked — cool blue-grey dim + slightly washed-out image so
     the feedback state is visible without being loud. (Round-6
     issue #3 — applies to every page the tile appears on.) */
  .tile.disliked {
    border-color: rgba(140, 160, 200, 0.45);
    box-shadow: 0 0 0 1px rgba(140, 160, 200, 0.30);
    opacity: 0.72;
  }
  .tile.disliked .full,
  .tile.disliked .ph {
    filter: saturate(0.55) brightness(0.92);
  }
  .tile.disliked:hover {
    opacity: 0.95;
  }
  /* A tile shouldn't be both liked AND disliked, but if a stale
     render shows both states, fade the favourite styling under
     the disliked styling so the "I hid this from recommendations"
     message wins. */
  .tile.disliked.favorite {
    border-color: rgba(140, 160, 200, 0.45);
    box-shadow: 0 0 0 1px rgba(140, 160, 200, 0.30);
    opacity: 0.72;
  }
  .ph, .full {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    transition: opacity var(--t-med) var(--ease-out);
  }
  .ph {
    z-index: 0;
    filter: saturate(1.15) blur(0.5px);
  }
  .full {
    z-index: 1;
    opacity: 0;
  }
  .tile.loaded .full { opacity: 1; }
  .fallback {
    background: linear-gradient(
      135deg,
      rgba(108,198,255,0.10),
      rgba(255,122,138,0.08)
    );
  }

  .score {
    position: absolute;
    top: 8px;
    left: 8px;
    padding: 4px 9px;
    font-size: var(--fs-xs);
    color: var(--fg-1);
    background: rgba(14,15,20,0.55);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-pill);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    opacity: 0;
    transition: opacity var(--t-fast) var(--ease-out);
  }
  .tile:hover .score,
  .tile.menu-open .score { opacity: 1; }

  .fav {
    position: absolute;
    top: 8px;
    right: 8px;
    width: 24px; height: 24px;
    border-radius: 50%;
    background: rgba(14,15,20,0.55);
    border: 1px solid var(--glass-edge);
    color: var(--warn);
    font-size: 14px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
  }
  .neg-badge {
    position: absolute;
    top: 8px;
    right: 8px;
    width: 24px; height: 24px;
    border-radius: 50%;
    background: rgba(14,15,20,0.55);
    border: 1px solid rgba(140, 160, 200, 0.55);
    color: #c9d3e6;
    font-size: 16px;
    font-weight: 700;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
  }
  /* Remove-from-set button — same corner slot as the persistent
   * fav/dislike badges, but interactive and only revealed on
   * hover so it doesn't add noise to the static grid. On
   * coarse-pointer (touch) devices it stays visible; the
   * (hover: none) media query handles that automatically.
   *
   * Click handler calls preventDefault + stopPropagation so the
   * surrounding <a class="tile"> doesn't navigate. data-no-open
   * is belt-and-braces for the same purpose.
   *
   * z-index: 2 (above .full which is z:1) so the button is hit-
   * testable even when the thumbnail fully covers the tile. */
  .remove-btn {
    position: absolute;
    top: 6px;
    right: 6px;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: rgba(14, 15, 20, 0.65);
    border: 1px solid var(--glass-edge-strong);
    color: var(--fg-1);
    font-size: 18px;
    font-weight: 500;
    line-height: 1;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    opacity: 0;
    z-index: 2;
    transition:
      opacity var(--t-fast) var(--ease-out),
      background var(--t-fast) var(--ease-out),
      border-color var(--t-fast) var(--ease-out),
      transform var(--t-fast) var(--ease-out);
  }
  .tile:hover .remove-btn,
  .tile:focus-within .remove-btn,
  .remove-btn:focus-visible {
    opacity: 1;
  }
  .remove-btn:hover {
    background: rgba(255, 122, 138, 0.18);
    border-color: rgba(255, 122, 138, 0.65);
  }
  .remove-btn:active {
    transform: scale(0.92);
  }
  /* Mobile / touch: hover doesn't exist, so the chip is always
   * present so the user can see the affordance. */
  @media (hover: none) {
    .remove-btn { opacity: 1; }
  }
</style>
