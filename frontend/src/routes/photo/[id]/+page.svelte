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
      // /api/search requires prompts, but the standalone photo
      // page can be reached without any. Use /api/random for
      // metadata, then look up our specific id.
      const res = await fetch('/api/random?limit=80', { credentials: 'include' });
      const data = await res.json();
      const match = (data?.results ?? []).find((r: any) => r.id === id);
      if (match) {
        point = match;
      } else if (id) {
        // Last resort: synthesise a display only from id + path
        point = {
          id,
          path: '(unknown)',
          url: photoUrl(id),
          score: 0
        };
      }
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
      <img src={photoUrl(point.id)} alt={point.path ?? ''} />
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
      <a class="raw" href={photoUrl(point.id)} target="_blank" rel="noopener">
        Open raw
      </a>
    </div>
  </article>
{/if}

<style>
  .back {
    display: inline-block;
    margin: 12px 0 18px;
    color: var(--fg-2);
  }
  .back:hover { color: var(--fg-1); }
  .page {
    padding: 18px;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 320px;
    gap: 18px;
    align-items: start;
  }
  @media (max-width: 900px) {
    .page { grid-template-columns: 1fr; }
  }
  .frame {
    background: var(--bg-1);
    border-radius: var(--r-2);
    border: 1px solid var(--glass-edge);
    overflow: hidden;
    display: grid;
    place-items: center;
  }
  .frame img {
    width: 100%;
    height: auto;
    max-height: 80vh;
    object-fit: contain;
  }
  .meta h2 {
    margin: 0 0 12px;
    font-size: var(--fs-md);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--fg-2);
    word-break: break-all;
  }
  dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 6px 12px;
    margin: 0 0 16px;
  }
  dt {
    color: var(--fg-3);
    font-size: var(--fs-sm);
  }
  dd {
    margin: 0;
    color: var(--fg-1);
  }
  .raw {
    display: inline-flex;
    align-items: center;
    height: 40px;
    padding: 0 18px;
    border-radius: var(--r-pill);
    background: var(--glass-2);
    color: var(--fg-1);
    text-decoration: none;
    border: 1px solid var(--glass-edge-strong);
  }
  .raw:hover { background: rgba(255,255,255,0.14); }
  .placeholder {
    color: var(--fg-3);
    padding: 32px 16px;
    background: var(--glass-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-3);
    text-align: center;
  }
  .placeholder.error { color: var(--negative); }
</style>
