<script lang="ts">
  /**
   * Right-click context menu for photo tiles. Actions:
   *   - Open in new tab
   *   - Copy image URL
   *   - Copy file path (when available)
   *   - Like / Unlike
   *   - Add to album › (submenu of user-created albums)
   *
   * Caller is responsible for:
   *   - rendering the trigger (a PhotoTile with onContextMenu)
   *   - providing the favourite toggle API and the list of user
   *     albums (passed in via the `albums` prop).
   */
  import { onMount } from 'svelte';
  import {
    photoUrl,
    addPhotoToAlbum,
    listAlbums
  } from '$lib/api/endpoints';
  import { toast } from './Toaster.svelte';

  type AlbumOption = { id: number; name: string };

  type Props = {
    /** Top-left viewport coords to anchor to. */
    x: number;
    y: number;
    pointId: string;
    path?: string;
    isFavorite?: boolean;
    /** If true, fetch user albums on open (so we don't load them
     *  until the user actually wants to use the submenu). */
    albums?: AlbumOption[];
    onClose: () => void;
    onToggleFavorite?: (id: string) => void;
  };
  let {
    x,
    y,
    pointId,
    path,
    isFavorite,
    albums: passedAlbums,
    onClose,
    onToggleFavorite
  }: Props = $props();

  let ref: HTMLDivElement | undefined = $state();
  let submenuOpen = $state(false);
  let submenuDropUp = $state(false);
  let fetchedAlbums = $state<AlbumOption[] | null>(null);
  let loadingAlbums = $state(false);

  async function openAlbumSubmenu() {
    submenuOpen = true;
    // Decide whether to drop up or right based on the menu's
    // proximity to the viewport bottom (round-5 #5: the user wanted
    // the album submenu to drop UP when the menu is anchored near
    // the bottom of the screen). We measure on the next frame so
    // the .submenu DOM exists.
    if (typeof window !== 'undefined') {
      await Promise.resolve();
      const rect = ref?.getBoundingClientRect();
      if (rect) {
        // If the menu bottom is within ~280 px of the viewport bottom,
        // flip the submenu to drop up (above) the menu.
        const vh = window.innerHeight;
        submenuDropUp = vh - rect.bottom < 280;
      }
    }
    if (passedAlbums) return; // parent provided them
    if (fetchedAlbums) return; // already fetched
    loadingAlbums = true;
    try {
      const res = (await listAlbums()) as { albums?: AlbumOption[] };
      fetchedAlbums = res?.albums ?? [];
    } catch {
      fetchedAlbums = [];
      toast.show("Couldn't load albums.", { kind: 'error' });
    } finally {
      loadingAlbums = false;
    }
  }

  function closeAlbumSubmenu() {
    submenuOpen = false;
  }

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
    // Open the dedicated photo page, not the raw file. The page
    // shows the photo at large size with metadata sidebar;
    // "Open raw" inside the page is the way to get the bare image.
    window.open(`/photo/${encodeURIComponent(pointId)}`, '_blank', 'noopener');
    onClose();
  }
  async function copyUrl() {
    try {
      await navigator.clipboard.writeText(photoUrl(pointId));
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
  async function addToAlbum(albumId: number, name: string) {
    try {
      await addPhotoToAlbum(albumId, pointId);
      toast.show(`Added to "${name}".`, { kind: 'success' });
    } catch {
      toast.show(`Couldn't add to "${name}".`, { kind: 'error' });
    }
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
      <span class="i" aria-hidden="true">{isFavorite ? '♡' : '♥'}</span>
      {isFavorite ? 'Unlike' : 'Like'}
    </button>
  {/if}
  <div class="sep" role="separator"></div>
  <div
    class="item submenu-host"
    role="menuitem"
    aria-haspopup="menu"
    aria-expanded={submenuOpen}
    onmouseenter={openAlbumSubmenu}
    onfocus={openAlbumSubmenu}
    onclick={openAlbumSubmenu}
    onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openAlbumSubmenu(); } }}
  >
    <span class="i" aria-hidden="true">+</span>
    Add to album
    <span class="caret" aria-hidden="true">▸</span>
    {#if submenuOpen}
      <div
        class="submenu glass-strong"
        class:drop-up={submenuDropUp}
        role="menu"
        onclick={(e) => e.stopPropagation()}
        onmouseleave={closeAlbumSubmenu}
      >
        {#if loadingAlbums}
          <div class="submenu-empty">Loading albums…</div>
        {:else if (passedAlbums ?? fetchedAlbums ?? []).length === 0}
          <div class="submenu-empty">No albums yet. Create one on the Albums page.</div>
        {:else}
          {#each (passedAlbums ?? fetchedAlbums ?? []) as a (a.id)}
            <button
              class="item submenu-item"
              role="menuitem"
              onclick={() => addToAlbum(a.id, a.name)}
            >
              <span class="i" aria-hidden="true">📁</span>
              {a.name}
            </button>
          {/each}
        {/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .menu {
    position: fixed;
    z-index: 200;
    min-width: 220px;
    padding: 6px;
    /* Solid-ish glass — visible against any photo. We sacrifice
       some "frosty" look for legibility (a translucent menu over
       bright sky/clouds disappears). */
    background: var(--dialog-fill);
    backdrop-filter: var(--glass-heavy);
    -webkit-backdrop-filter: var(--glass-heavy);
    border: 1px solid var(--glass-edge-strong);
    border-radius: var(--r-3);
    box-shadow: var(--shadow-3), 0 0 0 1px rgba(255, 255, 255, 0.04);
  }
  .item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 10px 14px;
    color: var(--fg-1);
    border-radius: var(--r-2);
    text-align: left;
    font-size: var(--fs-sm);
    font-weight: 500;
    background: transparent;
    border: 0;
    cursor: pointer;
    transition: background var(--t-fast), color var(--t-fast);
  }
  .item:hover {
    background: var(--glass-2);
    color: #fff;
  }
  .i {
    width: 18px;
    text-align: center;
    color: var(--fg-2);
    font-size: 14px;
  }
  .sep {
    height: 1px;
    background: var(--glass-edge);
    margin: 4px 0;
  }
  .submenu-host {
    position: relative;
  }
  .submenu-host .caret {
    margin-left: auto;
    color: var(--fg-2);
    font-size: 12px;
  }
  .submenu {
    /* Default: pop to the right of the parent item. */
    position: absolute;
    top: -6px;
    left: 100%;
    margin-left: 4px;
    min-width: 200px;
    padding: 6px;
    background: var(--dialog-fill);
    backdrop-filter: var(--glass-heavy);
    -webkit-backdrop-filter: var(--glass-heavy);
    border: 1px solid var(--glass-edge-strong);
    border-radius: var(--r-3);
    box-shadow: var(--shadow-3), 0 0 0 1px rgba(255, 255, 255, 0.04);
    z-index: 1;
  }
  /* If the menu is anchored near the right edge, flip the submenu
     to the LEFT side so it stays in viewport. Best-effort: if the
     container would overflow the viewport, flip. */
  /* If the parent menu is anchored near the bottom of the
     viewport, flip the submenu to drop UP-LEFT instead. */
  .submenu.drop-up {
    top: auto;
    bottom: -6px;
    left: 100%;
    margin-left: 4px;
  }
  .submenu-empty {
    padding: 10px 14px;
    color: var(--fg-2);
    font-size: var(--fs-sm);
  }
</style>
