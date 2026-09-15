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
    deleteAlbum,
    listFavorites,
    listDislikes,
    thumbUrl
  } from '$lib/api/endpoints';
  import { toast } from '$lib/components/Toaster.svelte';
  import { dialog } from '$lib/components/Dialog.svelte';
  import PageHeader from '$lib/components/PageHeader.svelte';
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

  // Round‑34: Surprise-me entry point. Same destination as
  // searchByAlbum, but appends `&mode=sample` so the home page
  // uses the K-of-N sample-centroid path. Each request re-rolls
  // the random subset, so refreshing surfaces a different
  // cluster. Disabled when the album is empty for the same
  // reason the regular Search button is — sample mode is a
  // no-op for empty source sets.
  function searchByAlbumSurprise(centroidName: string) {
    goto(`/?centroid=${encodeURIComponent(centroidName)}&mode=sample`);
  }
  function searchByUserAlbumSurprise(albumId: number) {
    searchByAlbumSurprise(`album:${albumId}`);
  }

  let albums = $state<AlbumSummary[]>([]);
  let loading = $state(true);
  let likesCount = $state(0);
  let dislikesCount = $state(0);
  // First photo id in each built-in album — used as the card cover.
  // Empty string while loading or when the album is empty.
  let likesFirstId = $state('');
  let dislikesFirstId = $state('');

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
    // Cover photo for each built-in album. Fire-and-forget — the
    // card is already informative without a thumbnail.
    Promise.all([
      fetchCover(listFavorites).then((id) => (likesFirstId = id)),
      fetchCover(listDislikes).then((id) => (dislikesFirstId = id))
    ]).catch(() => {
      /* non-fatal */
    });
  }

  /** Fetch one SearchResult from the list endpoint and return its id.
   * With `as_results=1` (which is what listFavorites / listDislikes
   * set), the backend returns a SearchResponse envelope — the
   * array lives under `results`, not `favorites` / `items`. */
  async function fetchCover(
    list: (limit?: number, offset?: number) => Promise<unknown>
  ): Promise<string> {
    try {
      const res = (await list(1, 0)) as { results?: { id: string }[] };
      return res.results?.[0]?.id ?? '';
    } catch {
      return '';
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
    const name = await dialog.prompt({
      title: 'Create album',
      label: 'Album name',
      confirmLabel: 'Create',
      defaultValue: ''
    });
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
    const ok = await dialog.confirm({
      title: 'Delete album',
      body: `Delete album "${name}"? This can't be undone.`,
      confirmLabel: 'Delete',
      kind: 'danger'
    });
    if (!ok) return;
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
    // Round-37: removed pushRandomTint() — the page-level
    // backdrop is a flat dark base (see +layout.svelte), no per-
    // page tint fetch needed.
  });
</script>

<svelte:head>
  <title>Albums · Image Search</title>
</svelte:head>

<PageHeader
  title="Albums"
  subtitle="Like photos to keep them handy, build collections, group memories."
>
  {#snippet actions()}
    <button type="button" class="new" onclick={create}>+ New album</button>
  {/snippet}
</PageHeader>

<div class="grid" aria-label="Albums">
  <article class="card glass">
    {#if likesFirstId}
      <img class="cover" src={thumbUrl(likesFirstId)} alt="" loading="lazy" />
    {:else}
      <div class="cover cover-empty" aria-hidden="true"></div>
    {/if}
    <a class="title" href="/albums/likes">
      <span>Likes</span>
    </a>
    <p class="desc">Photos you've liked. Built-in, always here.</p>
    <footer>
      <span class="count">{likesCount} photo{likesCount === 1 ? '' : 's'}</span>
      <span class="built-in" aria-label="Built-in, non-removable">built-in</span>
    </footer>
    <!-- Round‑29: search button on every album card. The Likes
         centroid is named "likes" on the backend (round‑29b);
         "favourites" is a back‑compat alias. Clicking either
         takes the user to the home page with results.
         Round‑34: a sibling "Surprise" button lands on the same
         destination with `&mode=sample` so the home page uses
         the K-of-N sample-centroid path. -->
    <div class="search-row">
      <button
        class="search-btn"
        type="button"
        data-centroid={LIKES_CENTROID}
        onclick={() => searchByAlbum(LIKES_CENTROID)}
        disabled={likesCount === 0}
        aria-label="Search by Likes centroid"
      >Search</button>
      <button
        class="search-btn surprise"
        type="button"
        data-centroid={LIKES_CENTROID}
        data-mode="sample"
        onclick={() => searchByAlbumSurprise(LIKES_CENTROID)}
        disabled={likesCount === 0}
        aria-label="Surprise me: search by a random sample of Likes"
        title="Search by a random sample of your Likes"
      >Surprise</button>
    </div>
  </article>
  <article class="card glass">
    {#if dislikesFirstId}
      <img class="cover" src={thumbUrl(dislikesFirstId)} alt="" loading="lazy" />
    {:else}
      <div class="cover cover-empty" aria-hidden="true"></div>
    {/if}
    <a class="title" href="/albums/dislikes">
      <span>Dislikes</span>
    </a>
    <p class="desc">Photos you've disliked. Built-in, always here.</p>
    <footer>
      <span class="count">{dislikesCount} photo{dislikesCount === 1 ? '' : 's'}</span>
      <span class="built-in" aria-label="Built-in, non-removable">built-in</span>
    </footer>
    <div class="search-row">
      <button
        class="search-btn"
        type="button"
        data-centroid={DISLIKES_CENTROID}
        onclick={() => searchByAlbum(DISLIKES_CENTROID)}
        disabled={dislikesCount === 0}
        aria-label="Search by Dislikes centroid"
      >Search</button>
      <button
        class="search-btn surprise"
        type="button"
        data-centroid={DISLIKES_CENTROID}
        data-mode="sample"
        onclick={() => searchByAlbumSurprise(DISLIKES_CENTROID)}
        disabled={dislikesCount === 0}
        aria-label="Surprise me: search by a random sample of Dislikes"
        title="Search by a random sample of your Dislikes"
      >Surprise</button>
    </div>
  </article>

  {#if loading}
    <div class="state state-wide">Loading albums…</div>
  {:else if albums.length > 0}
    {#each albums as a (a.id)}
      <article class="card glass">
        {#if a.first_member_id}
          <img class="cover" src={thumbUrl(a.first_member_id)} alt="" loading="lazy" />
        {:else}
          <div class="cover cover-empty" aria-hidden="true"></div>
        {/if}
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
        <div class="search-row">
          <button
            class="search-btn"
            type="button"
            data-centroid="album:{a.id}"
            onclick={() => searchByUserAlbum(a.id)}
            disabled={(a.member_count ?? 0) === 0}
            aria-label="Search by {a.name} centroid"
          >Search</button>
          <button
            class="search-btn surprise"
            type="button"
            data-centroid="album:{a.id}"
            data-mode="sample"
            onclick={() => searchByUserAlbumSurprise(a.id)}
            disabled={(a.member_count ?? 0) === 0}
            aria-label="Surprise me: search by a random sample of {a.name}"
            title="Search by a random sample of {a.name}"
          >Surprise</button>
        </div>
      </article>
    {/each}
  {/if}
</div>

<style>
  .new {
    height: 40px;
    padding: 0 var(--s-3);
    border-radius: var(--r-pill);
    background: var(--accent);
    color: var(--fg-on-accent);
    font-weight: 500;
  }
  .new:hover { background: var(--accent-2); }

  /* Round‑29: search-by-album button. Same shape as the Delete
     button so the two actions sit side-by-side; the accent colour
     differentiates "primary action" from "destructive". */
  .search-btn {
    margin-top: auto;
    align-self: stretch;
    height: 32px;
    padding: 0 var(--s-3);
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

  /* Round‑34: side-by-side Search + Surprise row. The two
     buttons share a horizontal flex container; the first one
     stretches to fill the row and the second one stays
     intrinsic-width so the layout reads as "[ Search… | Surp ]"
     instead of two equal-width buttons. The "Surprise" variant
     uses a softer glass-2 fill so the two read as related but
     distinct actions — Search is the primary, Surprise is a
     different retrieval mode on the same data. */
  .search-row {
    margin-top: auto;
    display: flex;
    gap: 8px;
    align-self: stretch;
  }
  .search-row .search-btn:first-child {
    flex: 1 1 auto;
  }
  .search-btn.surprise {
    flex: 0 0 auto;
    background: var(--glass-2);
    color: var(--fg-2);
  }
  .search-btn.surprise:hover:not(:disabled) {
    background: var(--accent);
    color: var(--fg-on-accent);
    border-color: var(--accent);
  }

  /* System albums (Likes, Dislikes) live in the same grid as the
     user-created albums below. They are pinned to the top of the
     flow by virtue of being declared first in the template; the
     `built-in` pill in the footer marks them visually. */
  /* Album cards (Likes/Dislikes + user albums) in a single auto-fit
     grid. auto-fit collapses empty tracks when item count is below
     the column count, so a sparse row (e.g. 2 user albums) centers
     the filled cards rather than leaving an empty band on the right.
     With 4+ items (the common case), all tracks fill and the grid
     reads as a continuous wall, same as auto-fill behavior. */
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, 260px);
    gap: var(--grid-gutter);
    margin: 0 auto var(--s-4);
    width: var(--grid-width, 100%);
    max-width: 100%;
    justify-content: center;
  }
  .card {
    /* No horizontal padding — the cover spans full width. Vertical
       padding only sits between the cover and the title. Round‑47:
       tightened from 16px to 12px (--s-2) — the cover-to-title
       gap felt airy when titles were short. */
    padding: 0 0 var(--s-3);
    display: flex;
    flex-direction: column;
    /* Round-49: per-section rhythm replaces the uniform 8px
       gap. Cover→title (16px) gets the most breathing room
       because the cover is the focal point and the text needs
       a clear entry; title→desc (4px) sits tight as a single
       "title block"; desc→footer (12px) medium separation;
       footer→search (16px) more before the action buttons so
       the buttons read as actions, not as more metadata.
       The uniform 8px rhythm read as cramped and monotonous
       — different gaps create visual hierarchy between the
       five sections. Implemented as per-child margins; flex
       gap is 0 so the margins are the only thing applying. */
    gap: 0;
    overflow: hidden; /* rounded corners on the cover */
    transition: transform var(--t-fast), box-shadow var(--t-fast);
  }
  /* Round-49: per-section rhythm on .card's five children.
     Cover is full-bleed (no top padding on .card). Gaps:
       cover → title:    16px (var(--s-3))
       title → desc:     4px  (var(--s-0)) — tight, they're
                                   one "title block"
       desc → footer:    12px (var(--s-2))
       footer → search:  16px (var(--s-3)) — more space so
                                   the buttons read as actions
                                   not as more metadata
     Margins, not flex gap — gap is uniform across siblings. */
  .card .title { margin: var(--s-3) 0 0; }
  .card .desc { margin: var(--s-0) 0 var(--s-2); }
  .card footer { margin-top: 0; }
  .card .search-row { margin-top: var(--s-3); }
  .card:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow-2);
  }
  /* Cover thumbnail — first photo added to the album. Sits flush
     against the top of the card, full-bleed, fixed aspect ratio
     so the cards in a row all line up regardless of which photo
     the backend picked. */
  .cover {
    display: block;
    width: 100%;
    aspect-ratio: 4 / 3;
    object-fit: cover;
    background: var(--glass-1);
    border-bottom: 1px solid var(--glass-edge);
  }
  /* Placeholder when the album has no members yet — keeps the
     card height the same as the others in its row. */
  .cover-empty {
    background: linear-gradient(
      135deg,
      var(--glass-1) 0%,
      var(--glass-2) 100%
    );
  }
  /* The card's horizontal padding sits between the full-bleed
     cover and the title/desc/footer. Round‑48: tightened to 20px
     (was 16) to match the --card-pad token used by Settings cards
     and the photo sidebar — same inner content edge across every
     panel in the app. */
  .card .title,
  .card .desc,
  .card footer,
  .card .search-row {
    margin-left: var(--card-pad-x, 20px);
    margin-right: var(--card-pad-x, 20px);
  }
  /* Individual buttons inside .search-row don't need the side
     margin — the row already has it, and a second layer of
     gutter would create a visible indent. */
  .card .search-row .search-btn {
    margin-left: 0;
    margin-right: 0;
  }
  .title {
    font-size: var(--fs-lg);
    font-weight: 500;
    color: var(--fg-1);
    display: inline-flex;
    align-items: center;
    gap: var(--s-1);
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
    padding: var(--s-0) var(--s-2);
    border-radius: var(--r-pill);
  }
  .del:hover { background: var(--negative-soft); color: var(--negative); }
</style>
