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
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { photoUrl } from '$lib/api/endpoints';
  import { blurhashToDataUrl } from './blurhash-bg';

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
  };

  type Props = {
    items: Item[];
    index: number;
    onClose: () => void;
    onToggleFavorite?: (id: string) => void;
    onDislike?: (id: string) => void;
  };
  let { items, index, onClose, onToggleFavorite, onDislike }: Props = $props();

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
      <button
        type="button"
        class="action like"
        class:active={current()?.isFavorite}
        onclick={() => current() && onToggleFavorite?.(current()!.id)}
        title="Like"
        aria-pressed={current()?.isFavorite ? 'true' : 'false'}
      >
        {current()?.isFavorite ? '♥ Liked' : '♡ Like'}
      </button>
      <button
        type="button"
        class="action neg"
        onclick={() => current() && onDislike?.(current()!.id)}
        title="Dislike"
      >
        − Dislike
      </button>
      <button
        type="button"
        class="action similar"
        onclick={() => current() && goSimilar(current()!.id)}
        title="Open the dedicated most-similar page for this photo"
      >
        ⟳ Most similar
      </button>
      <a
        class="action"
        href={current() ? photoUrl(current()!.id) : '#'}
        target="_blank"
        rel="noopener"
      >Open raw</a>
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
    /* Hard-constrain to the viewport. object-fit: contain means a
       photo at any aspect ratio lands inside this box — landscape
       fits the width, portrait fits the height — without cropping
       or scrolling. */
    max-width: calc(100vw - 32px);
    max-height: calc(100vh - 32px);
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
  .action {
    height: 32px;
    padding: 0 14px;
    border-radius: var(--r-pill);
    background: var(--glass-1);
    color: var(--fg-1);
    border: 1px solid var(--glass-edge);
    transition:
      background var(--t-fast),
      border-color var(--t-fast),
      color var(--t-fast),
      transform var(--t-fast);
    text-decoration: none;
    font-size: var(--fs-sm);
    font-weight: 500;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .action:hover { background: var(--glass-2); }
  /* Click feedback — visible scale-down on press. Works for both
     Like and Dislike (no backend field needed for "this was
     clicked"). */
  .action:active { transform: scale(0.96); }
  .action.neg { color: var(--negative); }
  /* Pressed feedback: when the photo is liked, the button lights up
     pink and stays lit so users know their click registered. */
  .action.like.active {
    background: rgba(255, 122, 138, 0.18);
    border-color: rgba(255, 122, 138, 0.55);
    color: var(--accent-pink);
  }
  .action.like.active:hover {
    background: rgba(255, 122, 138, 0.28);
  }
  /* Subtle tint on the "Most similar" button so it doesn't look
     like a duplicate of Like/Dislike. */
  .action.similar {
    background: rgba(108, 198, 255, 0.10);
    border-color: rgba(108, 198, 255, 0.35);
    color: var(--accent-blue);
  }
  .action.similar:hover { background: rgba(108, 198, 255, 0.18); }
</style>
