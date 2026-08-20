<script lang="ts">
  import '../lib/styles/tokens.css';
  import '../lib/styles/global.css';
  import { onMount } from 'svelte';
  import TopBar from '$lib/components/TopBar.svelte';
  import Toaster from '$lib/components/Toaster.svelte';
  import { pageTint } from '$lib/stores/tint';

  let { children } = $props();
  let tintUrl = $state<string | null>(null);

  onMount(() => {
    const unsub = pageTint.subscribe((v) => {
      tintUrl = v;
    });
    return unsub;
  });
</script>

<div
  class="app-shell"
  style={tintUrl ? `--glass-tint: url(${tintUrl})` : undefined}
>
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