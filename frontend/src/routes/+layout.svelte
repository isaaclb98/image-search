<script lang="ts">
  import '../lib/styles/tokens.css';
  import '../lib/styles/global.css';
  import TopBar from '$lib/components/TopBar.svelte';
  import Toaster from '$lib/components/Toaster.svelte';
  import ScrollToTop from '$lib/components/ScrollToTop.svelte';
  import Dialog from '$lib/components/Dialog.svelte';
  import { pageTint } from '$lib/stores/tint';
  import { onNavigate } from '$app/navigation';
  import { onMount } from 'svelte';

  let { children } = $props();

  // Two-layer page-tint crossfade. Previously a single <img> swapped
  // src instantly when pageTint changed (PhotoGrid fires it on every
  // scroll-driven row mount, so /random was thrashing the backdrop
  // colour continuously). Even with the 800ms opacity transition the
  // underlying image cut hard — felt amateurish.
  //
  // Now we keep two img slots, preload the new tint into the inactive
  // slot, then flip which slot is .active. Both opacities transition
  // on the same 800ms axis so the user sees one coordinated crossfade
  // rather than two stacked layer changes. The cleanup timer nulls
  // the now-invisible slot 800ms after the swap to free memory.
  //
  // On rapid scroll (multiple pageTint updates per second) the latest
  // preload wins; in-flight crossfades get cancelled via clearTimeout
  // and a fresh swap kicks off from whatever opacity we're at. The
  // result feels continuous rather than jumpy.
  const FADE_MS = 800;
  let layerA = $state<string | null>(null);
  let layerB = $state<string | null>(null);
  let activeLayer = $state<'a' | 'b'>('a');
  let fadeTimer: ReturnType<typeof setTimeout> | null = null;

  $effect(() => {
    const newUrl = $pageTint;
    if (!newUrl) {
      // Lightbox closed / navigated away from a gallery. Clear both
      // layers so the page returns to the deep base color.
      if (fadeTimer) clearTimeout(fadeTimer);
      layerA = null;
      layerB = null;
      activeLayer = 'a';
      return;
    }
    // No-op if the active layer already shows this URL.
    const activeUrl = activeLayer === 'a' ? layerA : layerB;
    if (activeUrl === newUrl) return;
    // Preload the new image so it's painted by the time we flip
    // active layers (otherwise the new slot shows blank during the
    // fade-in — that's the original bug, just on a different layer).
    const pre = new Image();
    pre.onload = () => {
      if (fadeTimer) clearTimeout(fadeTimer);
      const inactiveLayer = activeLayer === 'a' ? 'b' : 'a';
      if (inactiveLayer === 'a') layerA = newUrl;
      else layerB = newUrl;
      // Force a paint at the current opacity first — without this
      // rAF the browser batches the src change + the class swap and
      // skips the transition (same trick the Lightbox uses with its
      // tintReady gate).
      requestAnimationFrame(() => {
        activeLayer = inactiveLayer;
        fadeTimer = setTimeout(() => {
          const oldLayer = inactiveLayer === 'a' ? 'b' : 'a';
          if (oldLayer === 'a') layerA = null;
          else layerB = null;
        }, FADE_MS);
      });
    };
    pre.src = newUrl;
  });
  // Svelte 5 reactive store binding: $pageTint tracks the writable value.
  // The store carries a photo URL (relative path) which the backdrop
  // element renders behind everything as a heavily blurred colour wash.

  // Round-8: Cmd/Ctrl+K focuses the search composer input.
  // The convention is shared with GitHub, Linear, Vercel, and
  // most search-first apps. Implementation: a single
  // document-level keydown listener installed on mount, scoped
  // to modifier+K. We avoid stealing the key when the user is
  // already typing in an input/textarea (the conventional case
  // where Ctrl+K should pass through to the browser — though
  // most browsers have no default Ctrl+K binding, some users
  // map it to extensions).
  //
  // The shortcut only fires on routes that have a search
  // composer — currently / (home) and /search. On other routes
  // (lightbox is open, etc.) it's a no-op so we don't grab the
  // keystroke from a context that doesn't need it.
  function focusSearchInput() {
    const input = document.querySelector<HTMLInputElement>(
      '.composer-input'
    );
    if (input) {
      input.focus();
      // Select existing text so the user can type to replace
      // it (matches GitHub/Linear behaviour). If the input is
      // empty, this is a no-op.
      input.select();
    }
  }
  function onGlobalKey(e: KeyboardEvent) {
    if (!(e.metaKey || e.ctrlKey)) return;
    if (e.key !== 'k' && e.key !== 'K') return;
    // Don't fire if the user is already typing in a non-search
    // input (e.g. an album-rename modal's text field). The
    // composer input IS a text input — but we WANT to focus
    // it, so this guard is intentionally narrow: only skip
    // when the focused element is outside the composer.
    if (
      e.target instanceof HTMLElement &&
      e.target.tagName !== 'BODY' &&
      !e.target.closest('.composer')
    ) {
      return;
    }
    e.preventDefault();
    focusSearchInput();
  }
  onMount(() => {
    document.addEventListener('keydown', onGlobalKey);
    return () => document.removeEventListener('keydown', onGlobalKey);
  });

  // Round-6: View Transitions API crossfade between routes.
  // Wraps `goto()` and `<a>` navigations in
  // `document.startViewTransition` so the browser paints the
  // outgoing snapshot, runs the new route's render, then
  // crossfades between them. Falls back to plain navigation
  // on browsers without the API (Safari < 18.4, Firefox
  // < 137).
  //
  // The transition only fires for same-origin navigations —
  // external links and form submits skip it. The CSS rules
  // for ::view-transition-old(root) / ::view-transition-new(root)
  // in global.css drive the actual animation.
  onNavigate((navigation) => {
    if (
      typeof document === 'undefined' ||
      typeof document.startViewTransition !== 'function'
    ) {
      return; // Browser doesn't support View Transitions.
    }
    // Returning a Promise makes SvelteKit wait for it before
    // completing the navigation — this lets us wrap the
    // transition around the actual route swap.
    return new Promise((resolve) => {
      // Fire the transition. The browser snapshots the
      // current DOM, then swaps to the new route's DOM once
      // SvelteKit completes the navigation.
      document.startViewTransition!(async () => {
        resolve();
        // Wait for SvelteKit to finish the navigation
        // (it does this when our promise resolves).
        await navigation.complete;
      });
    });
  });
</script>

<div class="app-shell" class:has-tint={!!(layerA || layerB)}>
  <!-- Backdrop first so its painted pixels live behind everything
       that follows. position:fixed, full viewport. The two-layer
       crossfade logic in the script block above drives these. -->
  <div class="bg-backdrop" aria-hidden="true">
    {#if layerA}
      <img class="bg-img" class:active={activeLayer === 'a'} src={layerA} alt="" />
    {/if}
    {#if layerB}
      <img class="bg-img" class:active={activeLayer === 'b'} src={layerB} alt="" />
    {/if}
  </div>
  <!-- Vignette overlay keeps text readable while letting colour
       bleed through; mostly transparent in the centre. -->
  <div class="bg-tint" aria-hidden="true"></div>

  <TopBar />
  <main class="app-main">
    {@render children?.()}
  </main>
  <Toaster />
  <Dialog />
  <ScrollToTop />
</div>

<style>
  .app-shell {
    min-height: 100vh;
    position: relative;
    background: #0a0e16; /* deep base — visible only on first paint
                            before the photo loads */
  }
  .bg-backdrop {
    position: fixed;
    inset: 0;
    pointer-events: none;
    overflow: hidden;
    z-index: 0;
  }
  .bg-backdrop .bg-img {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    /* Heavier blur + lower opacity so the photo reads as ambient
       atmosphere rather than a featured picture. The tint is there
       but it doesn't dominate. */
    filter: blur(60px) saturate(180%) brightness(0.7);
    transform: scale(1.2);
    opacity: 0;
    transition: opacity 800ms ease-out;
  }
  .bg-backdrop .bg-img.active {
    opacity: 0.45;
  }
  .bg-tint {
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: 1;
    /* Subtle vignette so the photo sits behind everything as
       atmosphere rather than competing for attention. */
    background: radial-gradient(
      ellipse at center,
      rgba(8, 10, 16, 0.2) 0%,
      rgba(8, 10, 16, 0.55) 100%
    );
  }
  .app-main,
  :global(.topbar) {
    position: relative;
    z-index: 2;
  }
  .app-main {
    min-height: calc(100vh - var(--topbar-h));
    padding: 24px 24px 64px;
    max-width: 1600px;
    margin: 0 auto;
  }
  @media (max-width: 640px) {
    .app-main { padding: 16px 12px 48px; }
  }
</style>
