<script lang="ts">
  /**
   * Albums — list + create. Each row links to /albums/{id}.
   */
  import { onMount } from 'svelte';
  import {
    listAlbums,
    createAlbum,
    deleteAlbum
  } from '$lib/api/endpoints';
  import { toast } from '$lib/components/Toaster.svelte';
  import type { AlbumSummary } from '$lib/api/endpoints';

  let albums = $state<AlbumSummary[]>([]);
  let loading = $state(true);

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

  onMount(refresh);
</script>

<svelte:head>
  <title>Albums · image-search</title>
</svelte:head>

<section class="head">
  <div>
    <h1>Albums</h1>
    <p>Group your favourite photos.</p>
  </div>
  <button type="button" class="new" onclick={create}>+ New album</button>
</section>

{#if loading}
  <div class="placeholder">Loading albums…</div>
{:else if albums.length === 0}
  <div class="placeholder empty">No albums yet — create one to group your favourites.</div>
{:else}
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

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 14px;
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
