<script lang="ts">
  /**
   * Right-click context menu for photo tiles. Three actions:
   *   - Open in new tab  (the standalone /photo/{id} page)
   *   - Copy image URL
   *   - Pin/Unpin (Favourite / Save-searched)
   *
   * Caller is responsible for:
   *   - rendering the trigger (a PhotoTile with onContextMenu)
   *   - and providing the favourite toggle API.
   */
  import { onMount } from 'svelte';
  import { photoUrl } from '$lib/api/endpoints';

  type Props = {
    /** Top-left viewport coords to anchor to. */
    x: number;
    y: number;
    pointId: string;
    path?: string;
    isFavorite?: boolean;
    onClose: () => void;
    onToggleFavorite?: (id: string) => void;
  };
  let { x, y, pointId, path, isFavorite, onClose, onToggleFavorite }: Props = $props();

  let ref: HTMLDivElement | undefined = $state();

  // Clamp inside viewport on mount.
  onMount(() => {
    const el = ref;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    let nx = x, ny = y;
    if (x + rect.width > vw) nx = vw - rect.width - 8;
    if (y + rect.height > vh) ny = vh - rect.height - 8;
    el.style.left = nx + 'px';
    el.style.top  = ny + 'px';
  });

  async function openNewTab() {
    window.open(photoUrl(pointId), '_blank', 'noopener');
    onClose();
  }
  async function copyUrl() {
    try {
      await navigator.clipboard.writeText(
        photoUrl(pointId)
      );
    } catch {
      // ignore — older browsers without clipboard
    }
    onClose();
  }
  function toggleFav() {
    onToggleFavorite?.(pointId);
    onClose();
  }
  function copyPath() {
    if (path) navigator.clipboard?.writeText(path);
    onClose();
  }
</script>

<svelte:window
  onclick={onClose}
  onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}
/>

<div
  bind:this={ref}
  class="menu glass-strong"
  role="menu"
  onclick={(e) => e.stopPropagation()}
  oncontextmenu={(e) => { e.preventDefault(); e.stopPropagation(); }}
  style:left="{x}px"
  style:top="{y}px"
>
  <button class="item" role="menuitem" onclick={openNewTab}>
    <span class="i" aria-hidden="true">↗</span>
    Open in new tab
  </button>
  <button class="item" role="menuitem" onclick={copyUrl}>
    <span class="i" aria-hidden="true">⎘</span>
    Copy URL
  </button>
  {#if path}
    <button class="item" role="menuitem" onclick={copyPath}>
      <span class="i" aria-hidden="true">⎘</span>
      Copy file path
    </button>
  {/if}
  {#if onToggleFavorite}
    <div class="sep" role="separator"></div>
    <button class="item" role="menuitem" onclick={toggleFav}>
      <span class="i" aria-hidden="true">{isFavorite ? '☆' : '★'}</span>
      {isFavorite ? 'Unpin' : 'Pin'}
    </button>
  {/if}
</div>

<style>
  .menu {
    position: fixed;
    z-index: 200;
    min-width: 200px;
    padding: 6px;
    box-shadow: var(--shadow-3);
  }
  .item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 8px 12px;
    color: var(--fg-1);
    border-radius: var(--r-2);
    text-align: left;
    font-size: var(--fs-sm);
    transition: background var(--t-fast);
  }
  .item:hover { background: var(--glass-2); }
  .i {
    width: 16px;
    text-align: center;
    color: var(--fg-2);
  }
  .sep {
    height: 1px;
    background: var(--glass-edge);
    margin: 4px 6px;
  }
</style>
