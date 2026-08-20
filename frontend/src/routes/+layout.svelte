<script lang="ts">
  import '../lib/styles/tokens.css';
  import '../lib/styles/global.css';
  import TopBar from '$lib/components/TopBar.svelte';
  import Toaster from '$lib/components/Toaster.svelte';
  import { pageTint } from '$lib/stores/tint';

  let { children } = $props();
  // Svelte 5 reactive store binding: $pageTint tracks the writable value.
  // The store carries a photo URL (relative path) which the backdrop
  // element renders behind everything as a heavily blurred colour wash.
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
    /* Moderate blur — enough to be a "wash" but you can still see the
       photo content. Saturation up so colour bleed is unmistakable. */
    filter: blur(36px) saturate(300%) brightness(0.95) contrast(110%);
    transform: scale(1.15);
    opacity: 0;
    transition: opacity 600ms ease-out;
  }
  .app-shell.has-tint .bg-backdrop img {
    opacity: 1;
  }
  .bg-tint {
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: 1;
    /* Just a whisper of dark vignette for text contrast.
       The photo is the main visual. */
    background: radial-gradient(
      ellipse at center,
      rgba(8, 10, 16, 0) 50%,
      rgba(8, 10, 16, 0.3) 100%
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
