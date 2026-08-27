<script lang="ts">
  /**
   * Albums — list + create. Two non-optional system albums (Likes,
   * Dislikes) are rendered first and cannot be deleted or renamed;
   * user-created albums follow.
   *
   * Round‑29: every card has a "Search" button that navigates to
   * the home page with ?centroid=album:{id} (or ?centroid=likes /
   * ?centroid=dislikes for the built-ins). The home page renders
   * results inline using that album's centroid.
   *
   * Round‑29b: the Likes centroid is now registered under
   * `likes` (was `favourites`). The backend keeps `favourites`
   * as a back-compat alias so legacy URLs still resolve.
   */
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    listAlbums,
    createAlbum,
    deleteAlbum
  } from '$lib/api/endpoints';
  import { toast } from '$lib/components/Toaster.svelte';
  import type { AlbumSummary } from '$lib/api/endpoints';

  // Round‑29b: renamed from 'favourites' to 'likes' to match
  // the user-visible album label. The backend also registers
  // 'favourites' as a back-compat alias so legacy URLs still
  // resolve.
  const LIKES_CENTROID = 'likes';
  const DISLIKES_CENTROID = 'dislikes';

  function searchByAlbum(centroidName: string) {
    goto(`/?centroid=${encodeURIComponent(centroidName)}`);
  }

  function searchByUserAlbum(albumId: number) {
    // Round‑29 fix: backend registers user-album centroids under
    // `album:<id>` (colon, not underscore). Sending `album_<id>`
    // 404s. See `_album_centroid_name` in search/app.py.
    searchByAlbum(`album:${albumId}`);
  }

  let albums = $state<AlbumSummary[]>([]);
  let loading = $state(true);
  let likesCount = $state(0);
  let dislikesCount = $state(0);

  async function refresh() {
    loading = true;
    try {
      const res = (await listAlbums()) as { albums: AlbumSummary[] };
      albums = res?.albums ?? [];
    } catch (e: any) {
      toast.show('Failed to load albums', { kind: 'error' });
    } finally {
      loading = false;
    }
  }

  async function refreshSystemCounts() {
    try {
      // Fetch from the raw endpoints (no `as_results=1`) because
      // the SearchResponse wrapper doesn't carry a count.
      const [favs, dis] = await Promise.all([
        fetch('/api/favorites?limit=1').then((r) => r.json()),
        fetch('/api/dislikes?limit=1').then((r) => r.json())
      ]);
      likesCount = extractCount(favs);
      dislikesCount = extractCount(dis);
    } catch {
      // Counts are decorative; a 502 on count fetch shouldn't
      // blank the whole page.
    }
  }

  // /api/favorites returns {favorites: [...], total: N}
  // /api/dislikes returns {items: [...], count: N}
  // Different shapes — handle both.
  function extractCount(body: unknown): number {
    if (Array.isArray(body)) return body.length;
    if (body && typeof body === 'object') {
      const o = body as Record<string, unknown>;
      // /api/favorites uses `total`, /api/dislikes uses `count`.
      if (typeof o.total === 'number') return o.total;
      if (typeof o.count === 'number') return o.count;
      // Fallback: length of the array under any key.
      const list =
        (o.favorites as unknown[]) ??
        (o.dislikes as unknown[]) ??
        (o.items as unknown[]) ??
        [];
      if (Array.isArray(list)) return list.length;
    }
    return 0;
  }

  async function create() {
    const name = window.prompt('Album name?');
    if (!name) return;
    try {
      await createAlbum({ name: name.trim() });
      await refresh();
      toast.show('Album created.', { kind: 'success' });
    } catch {
      toast.show('Create failed.', { kind: 'error' });
    }
  }

  async function remove(id: number, name: string) {
    if (!window.confirm(`Delete album "${name}"?`)) return;
    try {
      await deleteAlbum(id);
      albums = albums.filter((a) => a.id !== id);
    } catch {
      toast.show('Delete failed.', { kind: 'error' });
    }
  }

  onMount(() => {
    // Both fetch from independent endpoints; run them in parallel
    // so the page reaches a fully-populated state in max(t1, t2)
    // instead of t1 + t2. (Tier 1.5.)
    Promise.all([refresh(), refreshSystemCounts()]);
  });
</script>

<svelte:head>
  <title>Albums · image-search</title>
</svelte:head>

<section class="head">
  <div>
    <h1>Albums</h1>
    <p>Like photos to keep them handy, build collections, group memories.</p>
  </div>
  <button type="button" class="new" onclick={create}>+ New album</button>
</section>

<section class="system" aria-label="Built-in albums">
  <article class="card glass system-like">
    <a class="title" href="/albums/likes">♥ Likes</a>
    <p class="desc">Photos you've liked. Built-in, always here.</p>
    <footer>
      <span class="count">{likesCount} photo{likesCount === 1 ? '' : 's'}</span>
      <span class="built-in" aria-label="Built-in, non-removable">built-in</span>
    </footer>
    <!-- Round‑29: search button on every album card. The Likes
         centroid is named "likes" on the backend (round‑29b);
         "favourites" is a back‑compat alias. Clicking either
         takes the user to the home page with results. -->
    <button
      class="search-btn"
      type="button"
      data-centroid={LIKES_CENTROID}
      onclick={() => searchByAlbum(LIKES_CENTROID)}
      disabled={likesCount === 0}
      aria-label="Search by Likes centroid"
    >Search</button>
  </article>
  <article class="card glass system-dislike">
    <a class="title" href="/albums/dislikes">− Dislikes</a>
    <p class="desc">Photos you've disliked. Built-in, always here.</p>
    <footer>
      <span class="count">{dislikesCount} photo{dislikesCount === 1 ? '' : 's'}</span>
      <span class="built-in" aria-label="Built-in, non-removable">built-in</span>
    </footer>
    <button
      class="search-btn"
      type="button"
      data-centroid={DISLIKES_CENTROID}
      onclick={() => searchByAlbum(DISLIKES_CENTROID)}
      disabled={dislikesCount === 0}
      aria-label="Search by Dislikes centroid"
    >Search</button>
  </article>
</section>

{#if loading}
  <div class="placeholder">Loading albums…</div>
{:else if albums.length === 0}
  <div class="placeholder empty">No custom albums yet — create one to group your photos.</div>
{:else}
  <h2 class="section-title">Your albums</h2>
  <div class="grid">
    {#each albums as a (a.id)}
      <article class="card glass">
        <a class="title" href="/albums/{a.id}">{a.name}</a>
        {#if a.description}<p class="desc">{a.description}</p>{/if}
        <footer>
          <span class="count">{a.member_count ?? 0} photos</span>
          <button
            class="del"
            type="button"
            onclick={() => remove(a.id, a.name)}
            aria-label="Delete {a.name}"
          >Delete</button>
        </footer>
        <button
          class="search-btn"
          type="button"
          data-centroid="album:{a.id}"
          onclick={() => searchByUserAlbum(a.id)}
          disabled={(a.member_count ?? 0) === 0}
          aria-label="Search by {a.name} centroid"
        >Search</button>
      </article>
    {/each}
  </div>
{/if}

<style>
  .head {
    margin: 16px 0 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .head h1 {
    font-size: var(--fs-2xl);
    font-weight: 600;
    margin: 0;
  }
  .head p {
    color: var(--fg-2);
    margin: 4px 0 0;
  }
  .new {
    height: 40px;
    padding: 0 18px;
    border-radius: var(--r-pill);
    background: var(--accent);
    color: var(--fg-on-accent);
    font-weight: 600;
  }
  .new:hover { background: var(--accent-2); }

  /* Round‑29: search-by-album button. Same shape as the Delete
     button so the two actions sit side-by-side; the accent colour
     differentiates "primary action" from "destructive". */
  .search-btn {
    margin-top: auto;
    align-self: stretch;
    height: 32px;
    padding: 0 14px;
    border-radius: var(--r-2);
    background: transparent;
    color: var(--fg-1);
    border: 1px solid var(--glass-edge);
    cursor: pointer;
    font-weight: 500;
    font-size: var(--fs-sm);
    transition: background var(--t-fast), border-color var(--t-fast), color var(--t-fast);
  }
  .search-btn:hover:not(:disabled) {
    background: var(--accent);
    color: var(--fg-on-accent);
    border-color: var(--accent);
  }
  .search-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  /* System albums — pinned to the top, never deletable. */
  .system {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: var(--grid-gutter);
    margin: 0 0 24px;
  }
  .section-title {
    font-size: var(--fs-sm);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--fg-2);
    margin: 0 0 12px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: var(--grid-gutter);
  }
  .card {
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    transition: transform var(--t-fast), box-shadow var(--t-fast);
  }
  .card:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-2);
  }
  .title {
    font-size: var(--fs-lg);
    font-weight: 600;
    color: var(--fg-1);
  }
  .desc {
    margin: 0;
    color: var(--fg-2);
    font-size: var(--fs-sm);
  }
  footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: auto;
  }
  .count {
    color: var(--fg-3);
    font-size: var(--fs-sm);
  }
  .del {
    font-size: var(--fs-sm);
    color: var(--fg-3);
    padding: 4px 8px;
    border-radius: var(--r-pill);
  }
  .del:hover { background: var(--negative-soft); color: var(--negative); }
  .placeholder {
    color: var(--fg-3);
    padding: 32px 16px;
    background: var(--glass-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-3);
    text-align: center;
    font-size: var(--fs-sm);
  }
  .placeholder.empty { color: var(--fg-2); }
</style>
