<script lang="ts">
  /**
   * Standalone photo page (the URL that opens when you click
   * "Open in new tab" on a tile). Shows one photo at large
   * size, with metadata below.
   */
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import { photoUrl } from '$lib/api/endpoints';

  let point = $state<any | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  async function load() {
    const id = $page.params.id ?? '';
    loading = true;
    try {
      // Use the dedicated metadata endpoint I just added.
      const res = await fetch(`/api/photo/${id}`, { credentials: 'include' });
      if (!res.ok) throw new Error(`Not found or failed to load`);
      const data = await res.json();
      point = data;
    } catch (e: any) {
      error = e?.message ?? 'Failed to load photo';
    } finally {
      loading = false;
    }
  }
  onMount(load);
</script>

<svelte:head>
  <title>Photo {point?.id ?? ''}</title>
</svelte:head>

<a class="back" href="/">← Back</a>

{#if loading}
  <div class="placeholder">Loading…</div>
{:else if error}
  <div class="placeholder error">Couldn't load: {error}</div>
{:else}
  <article class="page glass">
    <div class="frame">
      <img src={photoUrl(point.id, 1920)} alt={point.path ?? ''} />
    </div>
    <div class="meta">
      <h2>{point.path ?? point.id}</h2>
      <dl>
        <dt>Score</dt><dd>{(point.score ?? 0).toFixed(3)}</dd>
        {#if point.is_favorite}<dt>Favourite</dt><dd>★</dd>{/if}
        {#if point.width && point.height}
          <dt>Size</dt><dd>{point.width}×{point.height}</dd>
        {/if}
      </dl>
      <a class="button open" href={photoUrl(point.id)} target="_blank" rel="noopener">Open raw</a>
    </div>
  </article>
{/if}

<style>
  a.back {
    display: inline-block;
    margin-bottom: 1rem;
    color: var(--fg-2);
    text-decoration: none;
    font-weight: 500;
  }
  .page {
    display: grid;
    grid-template-columns: 1fr 320px;
    gap: 2rem;
    padding: 2rem;
    background: var(--bg-glass);
    backdrop-filter: blur(12px);
    border-radius: var(--r-2);
    box-shadow: 0 8px 32px var(--shadow-1);
  }
  .frame {
    background: var(--bg-2);
    border-radius: var(--r-2);
    overflow: hidden;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 1rem;
  }
  .frame img {
    max-width: 100%;
    max-height: 80vh;
    object-fit: contain;
  }
  .meta h2 {
    margin: 0;
    font-size: var(--fs-xl);
    line-height: 1.2;
    margin-bottom: 1rem;
    word-break: break-all;
  }
  dl {
    display: grid;
    grid-template-columns: 100px 1fr;
    gap: 1rem;
    margin-bottom: 2rem;
  }
  dt {
    font-weight: 600;
    color: var(--fg-2);
  }
  dd {
    margin: 0;
    color: var(--fg-1);
  }
  .open {
    display: inline-block;
    padding: 0.75rem 1.5rem;
    background: var(--bg-glass-strong);
    color: var(--fg-1);
    text-decoration: none;
    border-radius: var(--r-1);
    font-weight: 600;
    border: 1px solid var(--border);
    transition: background 0.2s, border-color 0.2s;
  }
  .open:hover {
    background: var(--bg-glass);
    border-color: var(--fg-2);
  }
</style>
