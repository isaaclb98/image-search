<script lang="ts">
  import '../lib/styles/tokens.css';
  import '../lib/styles/global.css';
  import TopBar from '$lib/components/TopBar.svelte';
  import Toaster from '$lib/components/Toaster.svelte';
  import ScrollToTop from '$lib/components/ScrollToTop.svelte';
  import Dialog from '$lib/components/Dialog.svelte';
  import { pageTint } from '$lib/stores/tint';
  import { onNavigate } from '$app/navigation';

  let { children } = $props();
  // Svelte 5 reactive store binding: $pageTint tracks the writable value.
  // The store carries a photo URL (relative path) which the backdrop
  // element renders behind everything as a heavily blurred colour wash.

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

<div class="app-shell" class:has-tint={$pageTint}>
  <!-- Backdrop first so its painted pixels live behind everything
       that follows. position:fixed, full viewport. -->
  <div class="bg-backdrop" aria-hidden="true">
    {#if $pageTint}
      <img src={$pageTint} alt="" />
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
  .bg-backdrop img {
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
  .app-shell.has-tint .bg-backdrop img {
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
