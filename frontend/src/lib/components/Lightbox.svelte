<script lang="ts">
  /**
   * Lightbox — full-screen modal showing one image at a time.
   *   - ←/→ or A/D navigates prev/next
   *   - Esc or click outside closes
   *   - background blurs + tints from the current photo
   *
   * Caller provides the items array (with point IDs) and the
   * index of the currently shown item, plus a way to toggle
   * favourite.
   */
  import { onMount } from 'svelte';
  import { photoUrl } from '$lib/api/endpoints';
  import { blurhashToDataUrl } from './blurhash-bg';

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
    /**
     * "Most similar photos" button click. Caller is expected to
     * run a similarity search for the current photo and replace
     * the page's items. We only signal — no fetching inside the
     * lightbox.
     */
    onSimilar?: (id: string) => void;
  };
  let { items, index, onClose, onToggleFavorite, onDislike, onSimilar }: Props = $props();

  let idx = $state(index);
  $effect(() => { idx = Math.max(0, Math.min(items.length - 1, index)); });

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
    return () => { document.body.style.overflow = prevOverflow; };
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
        <img class="photo" src={photoUrl(it.id)} alt="" />
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
        onclick={() => current() && onSimilar?.(current()!.id)}
        title="Find photos that look most like this one"
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
    z-index: 500;
    background: rgba(8,8,12,0.55);
    backdrop-filter: blur(28px) saturate(180%);
    -webkit-backdrop-filter: blur(28px) saturate(180%);
    display: grid;
    place-items: center;
    animation: fade var(--t-med) var(--ease-out);
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
    /* Let the photo show at its natural dimensions. The overlay is
       the only thing that's full-viewport; .content grows to the
       image's intrinsic size, so nothing is cropped. */
    max-width: 96vw;
    max-height: 96vh;
    display: grid;
    place-items: center;
    border-radius: var(--r-3);
    overflow: auto;
    background: rgba(8,8,12,0.4);
    border: 1px solid var(--glass-edge);
  }
  .photo {
    display: block;
    /* Scale the photo to the viewport's width; let height follow the
       natural aspect ratio. Tall portraits keep their full height
       inside the scrollable .content box (no cropping). */
    max-width: 100%;
    width: auto;
    height: auto;
    margin: auto;
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
    transition: background var(--t-fast);
    text-decoration: none;
    font-size: var(--fs-sm);
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .action:hover { background: var(--glass-2); }
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
