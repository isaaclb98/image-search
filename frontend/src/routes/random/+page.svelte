<script lang="ts">
  /**
   * Random — pick N random photos. Infinite scroll: keeps loading
   * more pages of random samples as the user scrolls. The dedupe
   * pass in loadMore stops the cycle once the dedupe filter
   * returns nothing new.
   *
   * No "Roll again" button — the page is meant to be browsed, not
   * re-rolled. No "End of results" UI either: when the library is
   * exhausted the scroll sentinel just stops firing.
   *
   * Like / Dislike / Most-similar toggles work in-place; the tile
   * stays in the grid either way.
   */
  import { onMount } from 'svelte';
  import {
    random,
    likePoint,
    unlikePoint,
    dislikePoint
  } from '$lib/api/endpoints';
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

  const PAGE = 20;
  let items = $state<Item[]>([]);
  let loading = $state(false);
  let hasMore = $state(true);

  // Server-side session cursor: the first call creates a shuffled
  // deck and returns a session id; subsequent calls pass the same
  // session id with an incremented offset to walk forward. The
  // server guarantees no duplicates within a session, so the client
  // doesn't need any dedupe logic — just append.
  let sessionId = $state<string | null>(null);
  let nextOffset = $state(0);

  async function refresh() {
    loading = true;
    try {
      // New session, start from offset 0.
      const res = await random({ limit: PAGE });
      items = (res?.results ?? []) as Item[];
      sessionId = res?.session_id ?? null;
      nextOffset = items.length;
      // session_total is the deck length. has_more from the server
      // tells us whether there's anything left to fetch.
      hasMore = !!res?.has_more && items.length > 0;
    } catch {
      items = [];
      hasMore = false;
    } finally {
      loading = false;
    }
  }

  async function loadMore() {
    if (loading || !hasMore || !sessionId) return;
    loading = true;
    try {
      const res = await random({
        session: sessionId,
        offset: nextOffset,
        limit: PAGE,
      });
      const more = (res?.results ?? []) as Item[];
      items = [...items, ...more];
      nextOffset += more.length;
      hasMore = !!res?.has_more && more.length > 0;
    } catch {
      hasMore = false;
    } finally {
      loading = false;
    }
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
      // No toast — silent. Visual feedback is on the button itself
      // (the .action.neg.active style lights up to mirror Like).
      // Mark the item as disliked so the button stays in the
      // "pressed" state until the user navigates away.
      items = items.map((x) =>
        x.id === id ? { ...x, is_disliked: true } : x
      );
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    }
  }

  onMount(refresh);
</script>

<svelte:head>
  <title>Random · image-search</title>
</svelte:head>

<section class="head glass">
  <h1>Random</h1>
  <p>Random photos from your library. Scroll for more.</p>
</section>

<section class="grid-wrap">
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
    margin: 16px 0 24px;
    padding: 22px 26px;
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .head h1 {
    font-size: var(--fs-2xl);
    font-weight: 600;
    margin: 0;
  }
  .head p { color: var(--fg-2); margin: 4px 0 0; }
  .grid-wrap { padding-top: 8px; }
</style>
