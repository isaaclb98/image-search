<script lang="ts">
  /**
   * For You — Gumbel-max sample from a Qdrant recommend() pool
   * of `pool_size` candidates ranked by relevance to the user's
   * taste (likes minus dislikes).
   *
   * The temperature slider controls the Gumbel noise scale:
   *   T=0   → deterministic top-K, the most taste-relevant
   *           photos in recommend() order. Curated.
   *   T=0.05 → mostly top-K, occasional surprise. Default.
   *   T=1.0 → near-uniform over the pool. Approaches /random
   *           behaviour.
   *
   * The pool slider controls how many recommend() candidates
   * the Gumbel draw is taken from. Larger pool = more variety
   * but slower first page.
   *
   * On page mount we generate a fresh random `seed` and pass
   * it to every request within this page-mount. The server
   * caches the deck keyed on (fav_ids, dis_ids, temperature,
   * pool_size, seed). Same seed → same deck (so paginated
   * scroll walks forward through page=0, page=1, ... with no
   * overlap). A new seed (new mount, or after a slider change)
   * → fresh deck.
   *
   * Round-88. Replaces the previous uniform-shuffle-the-top-1%
   * implementation. The uniform shuffle destroyed the ranking
   * signal — the user could not tell the difference from
   * /random. Gumbel-max preserves the ranking as a
   * controllable knob.
   */
  import { onMount } from 'svelte';
  import { forYouFeed, likePoint, unlikePoint, dislikePoint } from '$lib/api/endpoints';
  import { GRID_PAGE_SIZE } from '$lib/api/limits';
  import PhotoGrid from '$lib/components/PhotoGrid.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';

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
  // Round-88: temperature / pool bounds mirror the home-page
  // Surprise Me sliders. Same Gumbel mechanism, so the user
  // gets a consistent experience across the app.
  const TEMPERATURE_MIN = 0;
  const TEMPERATURE_MAX = 1;
  const POOL_SIZE_MIN = 200;
  const POOL_SIZE_MAX = 50_000;
  // 6 log-spaced stops. The rightmost is the upper bound for
  // the route. Log spacing because the taste pool can be much
  // larger than the cosine pool (top-2k is already plenty).
  const POOL_STOPS = [200, 500, 1_000, 2_000, 10_000, 50_000] as const;

  let items = $state<Item[]>([]);
  // Initialise loading=true so the PhotoGrid's spinner renders on the
  // very first paint — without this, the empty-state message below
  // shows for one frame before onMount() flips loading=true, which is
  // jarring on a 2-10s (occasionally 30s+) /api/for-you/feed call.
  let loading = $state(true);
  let hasMore = $state(true);
  let nextPage = $state(0);
  // One seed per page-mount AND per (temperature, poolSize)
  // combo. A slider change bumps the seed → fresh deck. The
  // seed is a uint32 — the route accepts any int.
  let temperature = $state(0.05);
  let poolSizeIndex = $state(3); // default = 2_000
  let poolSize = $derived(POOL_STOPS[poolSizeIndex]);
  let sessionId = $state<string | null>(null);
  let mountSeed = $state(0);
  let errorMsg = $state<string | null>(null);

  function rollSeed(): number {
    // crypto.getRandomValues is available in all modern
    // browsers; fall back to Math.random for SSR or older
    // runtimes.
    if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
      const buf = new Uint32Array(1);
      crypto.getRandomValues(buf);
      return buf[0];
    }
    return Math.floor(Math.random() * 0x100000000);
  }

  function onPoolIndexChange(idx: number) {
    const clamped = Math.max(0, Math.min(idx, POOL_STOPS.length - 1));
    if (clamped === poolSizeIndex) return;
    poolSizeIndex = clamped;
    // Slider change → fresh deck. Bump the seed and clear
    // the session so the next refresh builds a new deck.
    mountSeed = rollSeed();
    sessionId = null;
    items = [];
    nextPage = 0;
    hasMore = true;
    refresh();
  }

  function onTemperatureChange(v: number) {
    if (v === temperature) return;
    temperature = v;
    mountSeed = rollSeed();
    sessionId = null;
    items = [];
    nextPage = 0;
    hasMore = true;
    refresh();
  }

  function formatPool(idx: number): string {
    const n = POOL_STOPS[idx];
    return n.toLocaleString();
  }

  async function refresh() {
    loading = true;
    errorMsg = null;
    try {
      const res = await forYouFeed({
        limit: PAGE,
        offset: 0,
        temperature,
        pool_size: poolSize,
        seed: mountSeed,
      });
      items = (res?.results ?? []) as Item[];
      nextPage = 1;
      sessionId = res?.session_id ?? null;
      hasMore = !!res?.has_more && items.length > 0;
    } catch (e: any) {
      // 400 with "for-you requires at least one favourite"
      // is a friendly error, not a server problem. Surface
      // it inline.
      const detail = e?.body?.detail ?? e?.message ?? '';
      if (typeof detail === 'string' && detail.includes('favourite')) {
        errorMsg = 'Like a few photos first to seed the taste signal.';
      } else {
        items = [];
      }
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  async function loadMore(signal?: AbortSignal) {
    if (loading || !hasMore) return;
    loading = true;
    try {
      const res = await forYouFeed({
        limit: PAGE,
        offset: nextPage * PAGE,
        temperature,
        pool_size: poolSize,
        seed: mountSeed,
        session: sessionId ?? undefined,
        signal,
      });
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
      nextPage += 1;
      sessionId = res?.session_id ?? sessionId;
      hasMore = !!res?.has_more && more.length > 0;
    } catch (e) {
      if (signal?.aborted) return; // clean cancel from pre-fetch retrigger
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  // Round-9 perf: O(1) item lookup + update via shadow Map.
  let indexById = $state(new Map<string, number>());

  $effect(() => {
    const m = new Map<string, number>();
    for (let i = 0; i < items.length; i++) m.set(items[i].id, i);
    indexById = m;
  });

  function onToggleFavorite(id: string) {
    const idx = indexById.get(id);
    if (idx === undefined) return;
    const wasFavorite = !!items[idx].is_favorite;
    items[idx] = { ...items[idx], is_favorite: !wasFavorite };
    (async () => {
      try {
        if (wasFavorite) await unlikePoint(id);
        else await likePoint(id);
      } catch {
        // rollback
        items[idx] = { ...items[idx], is_favorite: wasFavorite };
      }
    })();
  }

  async function onDislike(id: string) {
    try {
      await dislikePoint(id);
    } catch {
    }
  }

  onMount(() => {
    mountSeed = rollSeed();
    refresh();
  });
</script>

<PageHeader
  title="For you"
  subtitle="Photos matched to your taste, with a knob for how much variety."
/>

<div class="surprise-panel">
  <label class="surprise-knob">
    <small>Surprise: <strong>{temperature.toFixed(2)}</strong></small>
    <input
      type="range"
      min={TEMPERATURE_MIN}
      max={TEMPERATURE_MAX}
      step="0.01"
      value={temperature}
      oninput={(e) => onTemperatureChange(parseFloat((e.target as HTMLInputElement).value))}
      aria-label="Surprise temperature"
      title="0 = curated top-K, 1 = near-uniform random over the pool"
    />
  </label>
  <label class="surprise-knob">
    <small>Pool: <strong>{formatPool(poolSizeIndex)}</strong></small>
    <input
      type="range"
      min="0"
      max={POOL_STOPS.length - 1}
      step="1"
      value={poolSizeIndex}
      oninput={(e) => onPoolIndexChange(parseInt((e.target as HTMLInputElement).value, 10))}
      aria-label="Pool size"
      title="How many recommend() candidates the Gumbel draw is taken from"
    />
  </label>
</div>

{#if errorMsg}
  <div class="state empty">{errorMsg}</div>
{:else if items.length === 0 && !loading}
  <div class="state empty">No recommendations yet — like or dislike a few photos to seed the signal.</div>
{:else}
  <PhotoGrid
    items={items as any}
    {hasMore}
    {loading}
    onLoadMore={loadMore}
    {onToggleFavorite}
    {onDislike}
  />
{/if}

<style>
  /* Round-88: the Surprise panel mirrors the home-page layout
     so the two feel like the same control. Two sliders
     (temperature, pool) sit above the grid; the values read
     out inline next to the labels. */
  .surprise-panel {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: var(--s-3);
    padding: var(--s-2) var(--s-3);
    margin: 0 auto var(--s-3);
    max-width: var(--grid-width, 1200px);
  }
  .surprise-knob {
    display: flex;
    align-items: center;
    gap: var(--s-2);
    color: var(--fg-2);
    font-size: var(--fs-sm);
  }
  .surprise-knob small {
    min-width: 110px;
    flex-shrink: 0;
  }
  .surprise-knob strong {
    color: var(--fg-1);
    font-weight: 600;
    margin-left: 4px;
  }
  .surprise-knob input[type='range'] {
    flex: 1;
    accent-color: var(--accent);
  }
  .state.empty {
    text-align: center;
    padding: var(--s-4);
    color: var(--fg-2);
  }
</style>
