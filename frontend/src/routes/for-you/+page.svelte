<script lang="ts">
  /**
   * For You — full feed page (vs the Home page which shows a
   * 20-row subset). Same backend endpoint, larger page size.
   * Provides a "Reset" action so users can clear their signal.
   */
  import { onMount } from 'svelte';
  import { forYouFeed, resetForYou } from '$lib/api/endpoints';
  import SearchGrid from '$lib/components/SearchGrid.svelte';
  import { toast } from '$lib/components/Toaster.svelte';

  type Item = {
    id: string;
    path?: string;
    score?: number;
    score_str?: string;
    blurhash?: string | null;
    is_favorite?: boolean;
  };

  const PAGE = 40;
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(false);
  let seen = $state<string[]>([]);

  async function refresh() {
    loading = true;
    try {
      const res = await forYouFeed(PAGE);
      items = (res?.results ?? []) as Item[];
      hasMore = items.length >= PAGE;
    } catch {
      items = [];
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  async function loadMore() {
    if (loading || !hasMore) return;
    loading = true;
    try {
      const res = await forYouFeed(PAGE);
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
      hasMore = more.length >= PAGE;
    } catch {
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  async function reset() {
    if (!window.confirm('Reset your For-You signal? Likes and dislikes will be cleared.')) return;
    await resetForYou();
    items = [];
    hasMore = false;
    await refresh();
    toast.show('For-You reset.', { kind: 'success' });
  }

  onMount(refresh);
</script>

<svelte:head>
  <title>For You · image-search</title>
</svelte:head>

<section class="head glass">
  <div>
    <h1>For you</h1>
    <p>Tuned by your saves, dislikes, and searches.</p>
  </div>
  <button type="button" class="reset" onclick={reset}>Reset signal</button>
</section>

<section>
  <SearchGrid {items} {loading} {hasMore} onLoadMore={loadMore} />
</section>

<style>
  .head {
    margin: 16px 0 24px;
    padding: 22px 26px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  .head h1 {
    font-size: var(--fs-2xl);
    font-weight: 600;
    margin: 0;
  }
  .head p { color: var(--fg-2); margin: 4px 0 0; }
  .reset {
    height: 40px;
    padding: 0 18px;
    border-radius: var(--r-pill);
    background: transparent;
    color: var(--fg-2);
    border: 1px solid var(--glass-edge);
    font-size: var(--fs-sm);
  }
  .reset:hover { background: var(--glass-1); color: var(--fg-1); }
</style>
