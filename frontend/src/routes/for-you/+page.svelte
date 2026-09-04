<script lang="ts">
  /**
   * For You — full feed page (vs the Home page which shows a
   * 20-row subset). Same backend endpoint, with diversity controls
   * (off / low / balanced / high) and depth (auto / 500 / 1000 /
   * 2000 / 5000 photos) — round-4 #5 and #6.
   *
   * Infinite scroll: loadMore fetches another PAGE items when the
   * user scrolls near the bottom. The backend returns a different
   * random sample of the recommendation pool each time, so the
   * user gets fresh candidates as they scroll.
   *
   * Note: previously had a "Reset signal" button that wiped likes
   * + dislikes. The user-facing bug list explicitly removed Reset;
   * reset is now an admin-only concern (POST /api/for-you/reset
   * still exists but is no longer surfaced in the UI).
   */
  import { onMount } from 'svelte';
  import {
    forYouFeed,
    likePoint,
    unlikePoint,
    dislikePoint
  } from '$lib/api/endpoints';
  import { GRID_PAGE_SIZE } from '$lib/api/limits';
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
  import { toast } from '$lib/components/Toaster.svelte';

  type Item = {
    id: string;
    path?: string;
    score?: number;
    score_str?: string;
    blurhash?: string | null;
    is_favorite?: boolean;
    is_disliked?: boolean;
  };

  const PAGE = GRID_PAGE_SIZE;
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(true);
  let currentPage = $state(0);
  let diversityMode = $state('balanced');
  let diversityDepth = $state('auto');

  /**
   * Reset the feed to page 0 and refetch with the current
   * diversity settings. Wired to the "Apply" button.
   */
  async function apply() {
    if (loading) return;
    items = [];
    currentPage = 0;
    hasMore = true;
    loading = true;
    try {
      const res = await forYouFeed(
        PAGE,
        diversityMode,
        diversityDepth,
        undefined,
        0
      );
      items = (res?.results ?? []) as Item[];
      hasMore = !!res?.has_more && items.length >= PAGE;
      currentPage = 1;
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
      const res = await forYouFeed(
        PAGE,
        diversityMode,
        diversityDepth,
        undefined,
        currentPage
      );
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
      hasMore = !!res?.has_more && more.length >= PAGE;
      if (more.length > 0) currentPage += 1;
    } catch {
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  // Round-9 perf: O(1) item lookup + update via shadow Map.
  // See the random page comment for the rationale — same
  // pattern, both pages had identical find+map code that
  // walked the whole items array per Like/Dislike click.
  let indexById = $state(new Map<string, number>());
  $effect(() => {
    const map = new Map<string, number>();
    for (let i = 0; i < items.length; i++) {
      map.set(items[i].id, i);
    }
    indexById = map;
  });

  async function onToggleFavorite(id: string) {
    const idx = indexById.get(id);
    if (idx === undefined) return;
    const liked = items[idx]?.is_favorite ?? false;
    try {
      if (liked) await unlikePoint(id);
      else await likePoint(id);
      const next = items.slice();
      next[idx] = { ...next[idx], is_favorite: !liked };
      items = next;
    } catch {
      toast.show('Failed to update like.', { kind: 'error' });
    }
  }

  async function onDislike(id: string) {
    const idx = indexById.get(id);
    if (idx === undefined) return;
    try {
      await dislikePoint(id);
      // Mark as disliked so the lightbox button stays lit
      // (round-5 #3 — visual feedback on Dislike).
      const next = items.slice();
      next[idx] = { ...next[idx], is_disliked: true };
      items = next;
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
  }

  onMount(apply);
</script>

<svelte:head>
  <title>For You · Image Search</title>
</svelte:head>

<section class="head glass">
  <h1>For you</h1>
  <p>Ranked by your likes + dislikes. Pick diversity to vary the results.</p>
</section>

<section class="filters glass">
  <label class="field">
    <span class="lab">Diversity</span>
    <select
      value={diversityMode}
      onchange={(e) => {
        diversityMode = (e.target as HTMLSelectElement).value;
      }}
      aria-label="Diversity mode"
      disabled={loading}
    >
      <option value="off">Off</option>
      <option value="low">Low</option>
      <option value="balanced">Balanced</option>
      <option value="high">High</option>
    </select>
  </label>
  <label class="field">
    <span class="lab">Diversity depth</span>
    <select
      value={diversityDepth}
      onchange={(e) => {
        diversityDepth = (e.target as HTMLSelectElement).value;
      }}
      aria-label="Diversity depth"
      disabled={loading}
    >
      <option value="auto">Auto</option>
      <option value="500">500 photos</option>
      <option value="1000">1,000 photos</option>
      <option value="2000">2,000 photos</option>
      <option value="5000">5,000 photos</option>
    </select>
  </label>
  <button
    type="button"
    class="apply"
    onclick={apply}
    disabled={loading}
    aria-label="Apply diversity"
  >
    {loading ? 'Loading…' : 'Apply'}
  </button>
</section>

<section>
  <PhotoGrid
    {items}
    {loading}
    {hasMore}
    onLoadMore={loadMore}
    {onToggleFavorite}
    {onDislike}
  />
</section>

<style>
  .head {
    margin: 16px 0 16px;
    padding: 22px 26px;
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .head h1 {
    margin: 0;
    font-size: var(--fs-2xl);
    font-weight: 600;
  }
  .head p {
    margin: 4px 0 0;
    color: var(--fg-2);
  }
  .filters {
    margin: 0 0 16px;
    padding: 14px 18px;
    display: flex;
    gap: 18px;
    align-items: center;
    flex-wrap: wrap;
  }
  .field {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .field .lab {
    color: var(--fg-2);
    font-size: var(--fs-sm);
  }
  .apply {
    background: var(--accent);
    color: var(--bg-1);
    border: 0;
    border-radius: var(--r-pill);
    padding: 6px 16px;
    font-weight: 600;
    font-size: var(--fs-sm);
    cursor: pointer;
  }
  .apply:disabled {
    cursor: not-allowed;
    opacity: 0.6;
  }
  .field select {
    background: rgba(14, 15, 20, 0.45);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-pill);
    padding: 0 28px 0 12px;
    height: 32px;
    color: var(--fg-1);
    font-size: var(--fs-sm);
    cursor: pointer;
  }
  .field select:hover { border-color: var(--glass-edge-strong); }
  .field select:focus {
    outline: none;
    border-color: var(--accent);
  }
</style>
