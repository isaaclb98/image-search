<script lang="ts">
  /**
   * Lightbox — full-screen modal showing one image at a time.
   *   - ←/→ or A/D navigates prev/next
   *   - Esc or click outside closes
   *   - background blurs + tints from the current photo
   *   - "Most similar" navigates to /similar/{id} (closes itself)
   *
   * Photo bytes come from /photo/{id}/raw?w=N — the server does a
   * Lanczos downsample and serves the cached JPEG. This avoids the
   * quality loss of letting the browser scale a 12 MP source down
   * to a 1408 px lightbox, and slashes bandwidth by ~10x.
   *
   * Caller provides the items array (with point IDs) and the
   * index of the currently shown item, plus a way to toggle
   * favourite.
   */
  import { onMount, tick } from 'svelte';
  import { goto } from '$app/navigation';
  import { photoUrl, addPhotoToAlbum, listAlbums } from '$lib/api/endpoints';
  import { blurhashToDataUrl } from './blurhash-bg';
  import ActionButton from './ActionButton.svelte';
  import { toast } from './Toaster.svelte';

  function goSimilar(id: string) {
    onClose();
    goto(`/similar/${encodeURIComponent(id)}`);
  }

  /**
   * Pick the right server-side resize width. We aim for 2x of the
   * rendered CSS width (retina) but cap at 1920 so 4K monitors
   * don't pull multi-MB files when 1920 px is enough visually.
   * Falls back to 1920 in SSR (window not available).
   */
  function lightboxWidth(): number {
    if (typeof window === 'undefined') return 1920;
    const cssWidth = window.innerWidth - 32;
    const dpr = Math.max(1, window.devicePixelRatio || 1);
    return Math.min(1920, Math.ceil(cssWidth * dpr));
  }

  type Item = {
    id: string;
    blurhash?: string | null;
    isFavorite?: boolean;
    isDisliked?: boolean;
  };

  type Props = {
    items: Item[];
    index: number;
    onClose: () => void;
    onToggleFavorite?: (id: string) => void;
    onDislike?: (id: string) => void;
    /** User-created albums — passed down to the right-click
     *  "Add to album" submenu (round-5). */
    albums?: { id: number; name: string }[];
  };
  let { items, index, onClose, onToggleFavorite, onDislike, albums }: Props = $props();

  let idx = $state(index);
  // Clamp `idx` to a valid range only when it's actually out of
  // bounds (e.g., items shrunk). Don't reset it on every items
  // update — earlier a `$effect(() => idx = clamp(index, ...))`
  // would fire mid-render with a transient items array and send
  // idx to 0, which made the Like click jump the user back to
  // the first photo (issue round-4 #1).
  $effect(() => {
    if (items.length > 0 && idx >= items.length) {
      idx = items.length - 1;
    }
  });

  let tint = $state<string | null>(null);

  // Round-6 issue #2 — Add to albums in the lightbox action bar.
  // The right-click menu has had this since round-4, but the lightbox
  // is the prominent view of a photo, so the action bar needs the
  // entry point too. Dropdown opens UP (the action bar is at the
  // bottom of the screen, so the menu must grow upward to stay in
  // view) and reuses the same `albums` prop already passed in.
  let albumOpen = $state(false);
  let albumBusy = $state(false);
  let albumMenuEl: HTMLDivElement | undefined = $state();
  let albumAnchorEl: HTMLSpanElement | undefined = $state();

  async function toggleAlbumMenu() {
    albumOpen = !albumOpen;
    if (albumOpen) {
      // Lazy-load albums if the caller didn't pass any. Use the
      // shared `albums` prop first; fall back to a fresh fetch.
      // listAlbums() returns {albums: [...]}, so unwrap it before
      // assigning to the local prop.
      if (!albums || albums.length === 0) {
        try {
          const res = (await listAlbums()) as { albums?: { id: number; name: string }[] };
          albums = res.albums ?? [];
        } catch {
          // List endpoint not available — render an empty menu.
          albums = [];
        }
      }
      // Focus the first item for keyboard navigation.
      await tick();
      albumMenuEl?.querySelector<HTMLButtonElement>('button.album-item')
        ?.focus();
    }
  }

  async function pickAlbum(albumId: number, albumName: string) {
    const it = current();
    if (!it || albumBusy) return;
    albumBusy = true;
    try {
      await addPhotoToAlbum(albumId, it.id);
      toast.show(`Added to "${albumName}"`, { kind: 'success' });
      albumOpen = false;
    } catch {
      toast.show(`Could not add to "${albumName}"`, { kind: 'error' });
    } finally {
      albumBusy = false;
    }
  }

  function onDocClick(e: MouseEvent) {
    if (!albumOpen) return;
    const target = e.target as Node;
    if (
      !albumMenuEl?.contains(target) &&
      !albumAnchorEl?.contains(target)
    ) {
      albumOpen = false;
    }
  }

  $effect(() => {
    if (albumOpen) {
      // Close on outside click. The mousedown listener lives on the
      // document so it catches clicks anywhere outside the menu.
      document.addEventListener('mousedown', onDocClick);
      return () => document.removeEventListener('mousedown', onDocClick);
    }
  });

  function current(): Item | null {
    return items[idx] ?? null;
  }

  $effect(() => {
    const it = current();
    if (!it || !it.blurhash) {
      tint = null;
      return;
    }
    blurhashToDataUrl(it.blurhash, 80, 50).then((u) => (tint = u));
  });

  function prev() {
    if (idx > 0) idx -= 1;
  }
  function next() {
    if (idx < items.length - 1) idx += 1;
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape') onClose();
    else if (e.key === 'ArrowLeft' || e.key.toLowerCase() === 'a') prev();
    else if (e.key === 'ArrowRight' || e.key.toLowerCase() === 'd') next();
  }
  onMount(() => {
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Hide the top tab bar while the lightbox is open — otherwise
    // its z-50 sticky strip bleeds through the semi-transparent
    // overlay.
    document.body.classList.add('lightbox-open');
    return () => {
      document.body.style.overflow = prevOverflow;
      document.body.classList.remove('lightbox-open');
    };
  });
</script>

<svelte:window onkeydown={onKey} />

<div
  class="overlay"
  style={tint ? `--glass-tint: url(${tint})` : undefined}
  onclick={onClose}
  role="dialog"
  aria-modal="true"
>
  {#if tint}<div class="tint" style:background="var(--glass-tint) no-repeat center/cover" aria-hidden="true"></div>{/if}
  <div class="content" onclick={(e) => e.stopPropagation()} oncontextmenu={(e) => e.preventDefault()}>
    <button class="nav close" type="button" onclick={onClose} aria-label="Close">×</button>
    <button
      class="nav prev"
      type="button"
      onclick={prev}
      disabled={idx === 0}
      aria-label="Previous"
    >‹</button>
    {#if current()}
      {@const it = current()!}
      {#key it.id}
        <img class="photo" src={photoUrl(it.id, lightboxWidth())} alt="" />
      {/key}
    {/if}
    <button
      class="nav next"
      type="button"
      onclick={next}
      disabled={idx === items.length - 1}
      aria-label="Next"
    >›</button>

    <div class="bar glass-strong">
      <span class="count">{idx + 1} / {items.length}</span>
      <ActionButton
        onclick={() => current() && onToggleFavorite?.(current()!.id)}
        title="Like"
        ariaPressed={current()?.isFavorite ? 'true' : 'false'}
      >
        Like
      </ActionButton>
      <ActionButton
        onclick={() => current() && onDislike?.(current()!.id)}
        title="Dislike"
        ariaPressed={current()?.isDisliked ? 'true' : 'false'}
      >
        Dislike
      </ActionButton>
      <ActionButton
        onclick={() => current() && goSimilar(current()!.id)}
        title="Open the dedicated most-similar page for this photo"
      >
        Most similar
      </ActionButton>
      <span bind:this={albumAnchorEl} class="album-anchor">
        <ActionButton
          onclick={toggleAlbumMenu}
          title="Add this photo to an album"
          ariaHaspopup="menu"
          aria-expanded={albumOpen}
        >
          Add to album
        </ActionButton>
        {#if albumOpen}
          <div
            bind:this={albumMenuEl}
            class="album-menu glass-strong"
            role="menu"
          >
            {#if !albums || albums.length === 0}
              <div class="album-empty">No albums yet — create one from the Albums page.</div>
            {:else}
              {#each albums as a (a.id)}
                <button
                  type="button"
                  class="album-item"
                  role="menuitem"
                  disabled={albumBusy}
                  onclick={() => pickAlbum(a.id, a.name)}
                >
                  {a.name}
                </button>
              {/each}
            {/if}
          </div>
        {/if}
      </span>
      <ActionButton
        href={current() ? photoUrl(current()!.id) : '#'}
        target="_blank"
        rel="noopener"
      >Open raw</ActionButton>
    </div>
  </div>
</div>

<style>
  .overlay {
    position: fixed;
    inset: 0;
    /* Above the top bar (z-50) and every other layer. */
    z-index: 500;
    background: rgba(8,8,12,0.55);
    backdrop-filter: blur(28px) saturate(180%);
    -webkit-backdrop-filter: blur(28px) saturate(180%);
    display: grid;
    place-items: center;
    animation: fade var(--t-med) var(--ease-out);
  }
  /* When the lightbox is open, the top tab bar would otherwise
     bleed through the semi-transparent overlay (rgba 0.55). Hide
     it via a body class so the user isn't fighting two layers of
     nav at once. */
  :global(body.lightbox-open .topbar) {
    display: none;
  }
  @keyframes fade {
    from { opacity: 0; }
    to   { opacity: 1; }
  }
.tint {
    position: absolute;
    inset: -40px;
    background: var(--glass-tint, none) no-repeat center / cover;
    filter: blur(60px) saturate(1.5) brightness(0.55);
    opacity: 0.65;
    pointer-events: none;
    z-index: -1;
  }
  .content {
    position: relative;
    /* Explicit width/height (not just max-*) so the .photo inside
       has a hard container to fit into. Without an explicit size,
       grid + place-items: center lets the container shrink to the
       photo's intrinsic size — defeating object-fit: contain for
       photos that are bigger than the viewport. */
    --bar-height: 64px;
    width: calc(100vw - 32px);
    height: calc(100vh - 32px);
    display: grid;
    place-items: center;
    border-radius: var(--r-3);
    overflow: hidden;
    background: rgba(8,8,12,0.4);
    border: 1px solid var(--glass-edge);
  }
  .photo {
    display: block;
    /* Stay inside .content and leave room for the action bar.
       width:auto / height:auto + object-fit:contain keeps the
       aspect ratio; max-height uses the bar-height CSS variable
       defined on .content so the photo never overflows below the
       dialog's bottom edge. */
    max-width: 100%;
    max-height: calc(100% - var(--bar-height) - 12px);
    width: auto;
    height: auto;
    object-fit: contain;
    border-radius: var(--r-2);
    box-shadow: var(--shadow-3);
  }
  .nav {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    width: 48px;
    height: 48px;
    border-radius: 50%;
    background: rgba(14,15,20,0.65);
    border: 1px solid var(--glass-edge-strong);
    color: var(--fg-1);
    font-size: 26px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: background var(--t-fast);
    z-index: 1;
  }
  .nav:hover { background: rgba(14,15,20,0.85); }
  .nav:disabled { opacity: 0.3; cursor: not-allowed; }
  .prev { left: 12px; }
  .next { right: 12px; }
  .close {
    top: 12px;
    right: 12px;
    transform: none;
    font-size: 22px;
  }
  .bar {
    /* The photo must leave room for this bar. Exposing its height
       as a CSS variable lets .content reserve matching vertical
       space (see .content below). */
    --bar-height: 64px;
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    bottom: 18px;
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 8px 14px;
    border-radius: var(--r-pill);
    box-shadow: var(--shadow-2);
  }
  .count {
    color: var(--fg-2);
    font-size: var(--fs-sm);
    padding: 0 6px;
  }
  /* Round-6 — Add to album dropdown. Anchored to the action button
     so it stays aligned; grows UP because the action bar sits at
     the bottom of the viewport. Glass-strong matches the bar so it
     reads as a sibling surface, not a separate popover. */
  .album-anchor {
    position: relative;
    display: inline-flex;
  }
  .album-menu {
    position: absolute;
    right: 0;
    /* Position ABOVE the action bar (which is ~64 px tall plus its
       18 px bottom margin). Without the +64px offset the dropdown
       bottom clips into the toolbar's glass-strong pill behind
       it. */
    bottom: calc(100% + 64px);
    min-width: 200px;
    max-height: 280px;
    overflow-y: auto;
    border-radius: var(--r-2);
    border: 1px solid var(--glass-edge);
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 2px;
    z-index: 510; /* above the lightbox overlay (z=500) */
    box-shadow: var(--shadow-2);
  }
  .album-item {
    appearance: none;
    background: transparent;
    border: 1px solid transparent;
    color: var(--fg-1);
    padding: 8px 12px;
    border-radius: var(--r-1);
    text-align: left;
    font: inherit;
    font-size: var(--fs-sm);
    cursor: pointer;
    transition: background var(--t-fast) var(--ease-out);
  }
  .album-item:hover,
  .album-item:focus-visible {
    background: rgba(255, 255, 255, 0.06);
    border-color: var(--glass-edge);
    outline: none;
  }
  .album-item:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .album-empty {
    padding: 12px;
    font-size: var(--fs-sm);
    color: var(--fg-3);
    text-align: center;
  }
</style>
