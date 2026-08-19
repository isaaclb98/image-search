<script lang="ts">
  import '../lib/styles/tokens.css';
  import '../lib/styles/global.css';
  import TopBar from '$lib/components/TopBar.svelte';
  import Toaster from '$lib/components/Toaster.svelte';

  let { children } = $props();
</script>

<TopBar />
<main class="app-main">
  {@render children?.()}
</main>
<Toaster />

<style>
  .app-main {
    /* the top bar is sticky; main content sits below it */
    min-height: calc(100vh - var(--topbar-h));
    padding: 24px 24px 64px;
    max-width: 1600px;
    margin: 0 auto;
  }
  @media (max-width: 640px) {
    .app-main { padding: 16px 12px 48px; }
  }
</style>
