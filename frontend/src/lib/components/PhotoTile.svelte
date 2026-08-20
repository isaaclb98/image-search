<script lang="ts">
  /**
   * One tile in the grid. Renders:
   *   - blurhash as a tinted placeholder (decoded async, cheap)
   *   - the real photo on top, once loaded
   *   - a glass "hover" chrome with the score (top-left) and
   *     context-menu trigger (top-right). No buttons until hover.
   *
   * Tiles are responsible for their own click + right-click
   * interactions. The grid above just lays them out.
   */
  import { onMount } from 'svelte';
  import { photoUrl } from '$lib/api/endpoints';
  import { blurhashToDataUrl } from './blurhash-bg';

  type Props = {
    pointId: string;
    blurhash?: string | null;
    scoreStr?: string;
    score?: number;
    isFavorite?: boolean;
    contextMenuOpen?: boolean;
    onOpen?: (id: string) => void;
    onContextMenu?: (id: string, e: MouseEvent) => void;
  };
  let {
    pointId,
    blurhash,
    scoreStr,
    isFavorite,
    contextMenuOpen,
    onOpen,
    onContextMenu
  }: Props = $props();

  let dataUrl = $state<string | null>(null);
  let loaded = $state(false);
  let imgEl: HTMLImageElement | undefined = $state();

  onMount(() => {
    if (blurhash) {
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
</script>

<a
  class="tile"
  class:loaded
  class:favorite={isFavorite}
  class:menu-open={contextMenuOpen}
  href={photoUrl(pointId)}
  onclick={handleClick}
  oncontextmenu={handleContext}
  style={dataUrl ? `--glass-tint: url(${dataUrl})` : undefined}
  title={scoreStr ? `score ${scoreStr}` : undefined}
>
  {#if dataUrl}
    <img class="ph" src={dataUrl} alt="" aria-hidden="true" />
  {:else}
    <div class="ph fallback" aria-hidden="true"></div>
  {/if}
  <img
    bind:this={imgEl}
    class="full"
    src={photoUrl(pointId)}
    alt=""
    loading="lazy"
    decoding="async"
    onload={() => (loaded = true)}
    onerror={() => (loaded = true)}
  />
  {#if scoreStr}
    <span class="score">{scoreStr}</span>
  {/if}
  {#if isFavorite}
    <span class="fav" data-no-open title="Favourite">★</span>
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
                box-shadow var(--t-fast) var(--ease-out);
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
</style>
