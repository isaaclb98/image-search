<script lang="ts">
  /**
   * ScrollToTop — floating button at bottom-right that scrolls the
   * page back to the top. Only visible once the user has scrolled
   * past a threshold (default 240px) so it doesn't clutter the
   * initial view.
   *
   * Listens to `window.scroll` with passive=true for jank-free
   * perf. Click smooth-scrolls to the top; falls back to instant
   * scroll if `scroll-behavior: smooth` isn't honoured.
   */
  import { onMount } from 'svelte';

  type Props = {
    /** Pixels of scroll before the button appears. */
    threshold?: number;
  };
  let { threshold = 240 }: Props = $props();

  let visible = $state(false);

  function onScroll() {
    visible = window.scrollY > threshold;
  }

  function toTop() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  onMount(() => {
    onScroll(); // initial state
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  });
</script>

{#if visible}
  <button
    type="button"
    class="scroll-top glass-strong"
    onclick={toTop}
    aria-label="Scroll to top"
    title="Scroll to top"
  >
    <span class="arrow" aria-hidden="true">↑</span>
  </button>
{/if}

<style>
  .scroll-top {
    position: fixed;
    right: 20px;
    bottom: 20px;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    background: rgba(14, 15, 20, 0.65);
    border: 1px solid var(--glass-edge-strong);
    color: var(--fg-1);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: var(--shadow-2);
    z-index: 40;
    transition:
      background var(--t-fast),
      transform var(--t-fast);
  }
  .scroll-top:hover {
    background: rgba(14, 15, 20, 0.85);
    transform: translateY(-2px);
  }
  .scroll-top:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .arrow {
    font-size: 22px;
    line-height: 1;
  }
</style>
