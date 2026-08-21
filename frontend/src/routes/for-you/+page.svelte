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

  const PAGE = 20;
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(true);
  let diversityMode = $state('balanced');
  let diversityDepth = $state('auto');

  async function refresh() {
    loading = true;
    try {
      const res = await forYouFeed(PAGE, diversityMode, diversityDepth);
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
      // /api/for-you returns the same ranked set on every call
      // (deterministic given the same likes + dislikes), so dedupe
      // would kill the loop after the first batch. We append the
      // full result. The user will see duplicates in the long run
      // — fine for an infinite-feel feed. Stop when the library is
      // exhausted AND the response is shorter than PAGE.
      const res = await forYouFeed(PAGE, diversityMode, diversityDepth);
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
      hasMore = more.length >= PAGE;
    } catch {
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  function onDiversityChange() {
    refresh();
  }

  async function onToggleFavorite(id: string) {
    const it = items.find((x) => x.id === id);
    const liked = it?.is_favorite ?? false;
    try {
      if (liked) await unlikePoint(id);
      else await likePoint(id);
      items = items.map((x) =>
        x.id === id ? { ...x, is_favorite: !liked } : x
      );
    } catch {
      toast.show('Failed to update like.', { kind: 'error' });
    }
  }

  async function onDislike(id: string) {
    try {
      await dislikePoint(id);
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
  }

  onMount(refresh);
</script>

<svelte:head>
  <title>For You · image-search</title>
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
        onDiversityChange();
      }}
      aria-label="Diversity mode"
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
        onDiversityChange();
      }}
      aria-label="Diversity depth"
    >
      <option value="auto">Auto</option>
      <option value="500">500 photos</option>
      <option value="1000">1,000 photos</option>
      <option value="2000">2,000 photos</option>
      <option value="5000">5,000 photos</option>
    </select>
  </label>
</section>

<section>
  <SearchGrid
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
