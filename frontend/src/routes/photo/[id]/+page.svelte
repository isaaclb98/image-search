<script lang="ts">
  /**
   * Dedicated photo page. URL: /photo/{id}.
   *
   * Layout: large photo on the left, ~320px metadata sidebar on
   * the right. Single column on narrow viewports.
   *
   * Reached by:
   *   - Right-click → "Open in new tab" on any photo tile
   *   - Direct URL paste / bookmark / share
   *
   * What this page reuses:
   *   - $lib/api/endpoints (photoUrl, likePoint, unlikePoint,
   *     dislikePoint, blurhash background)
   *   - $lib/components/Button (primary/secondary/ghost actions)
   *   - $lib/components/Chip (collection/model badges)
   *   - $lib/components/Toaster (action feedback)
   *   - The existing TopBar from +layout.svelte
   *
   * What this page does NOT do:
   *   - Lightbox navigation (that's the click action on a tile)
   *   - Add-to-album picker (we link to the Lightbox for that)
   *   - In-place editing of any metadata (photos are immutable)
   */
  import { page } from '$app/stores';
  import { onMount } from 'svelte';
  import {
    photoUrl,
    thumbUrl,
    likePoint,
    unlikePoint,
    dislikePoint
  } from '$lib/api/endpoints';
  import Button from '$lib/components/Button.svelte';
  import Chip from '$lib/components/Chip.svelte';
  import { toast } from '$lib/components/Toaster.svelte';
  import { blurhashToDataUrl } from '$lib/components/blurhash-bg';

  type PhotoMeta = {
    id: string;
    path: string;
    score: number;
    is_favorite: boolean;
    url: string;
    width: number | null;
    height: number | null;
    blurhash: string | null;
    size: number | null;
    mtime: number | null;
    folder: string | null;
    shard: string | null;
    collection: string | null;
    model_name: string | null;
    model_revision: string | null;
    model_dim: number | null;
    indexed_at: string | null;
    content_sha256: string | null;
    dhash: string | null;
  };

  let photo = $state<PhotoMeta | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let actionInFlight = $state(false);
  // Blurhash placeholder — async-decoded from the photo's blurhash
  // field after the metadata loads. Falls back to a dark surface
  // if decoding fails (no blurhash, malformed, etc.).
  let blurTint = $state<string | null>(null);

  async function load() {
    const id = $page.params.id ?? '';
    loading = true;
    error = null;
    try {
      const res = await fetch(`/api/photo/${encodeURIComponent(id)}`, {
        credentials: 'include'
      });
      if (res.status === 404) {
        throw new Error('Photo not found');
      }
      if (!res.ok) {
        throw new Error(`Failed to load photo (HTTP ${res.status})`);
      }
      const data = (await res.json()) as PhotoMeta;
      photo = data;
      // Decode blurhash off the critical path — the hero image
      // covers it quickly anyway.
      if (data.blurhash) {
        blurhashToDataUrl(data.blurhash, 64, 40)
          .then((url) => {
            // Only apply if the photo hasn't changed (e.g. user
            // navigated to a different one during decode).
            if (photo && photo.id === data.id) blurTint = url;
          })
          .catch(() => {
            /* leave blurTint null — the dark surface shows */
          });
      }
    } catch (e: unknown) {
      error = e instanceof Error ? e.message : 'Failed to load photo';
    } finally {
      loading = false;
    }
  }
  onMount(load);

  async function toggleFavorite() {
    if (!photo || actionInFlight) return;
    actionInFlight = true;
    const wasFav = photo.is_favorite;
    // Optimistic toggle — the Lightbox does the same.
    photo = { ...photo, is_favorite: !wasFav };
    try {
      if (wasFav) await unlikePoint(photo.id);
      else await likePoint(photo.id);
    } catch {
      // Roll back on failure.
      photo = { ...photo, is_favorite: wasFav };
      toast.show('Failed to update like.', { kind: 'error' });
    } finally {
      actionInFlight = false;
    }
  }

  async function onDislike() {
    if (!photo || actionInFlight) return;
    actionInFlight = true;
    try {
      await dislikePoint(photo.id);
      toast.show('Photo marked as disliked.', { kind: 'info' });
    } catch {
      toast.show('Failed to dislike.', { kind: 'error' });
    } finally {
      actionInFlight = false;
    }
  }

  async function copyText(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast.show(`${label} copied.`, { kind: 'info' });
    } catch {
      toast.show('Copy failed — clipboard not available.', { kind: 'error' });
    }
  }

  // Display helpers — pure functions, no state.
  function formatBytes(n: number | null): string {
    if (n == null) return '—';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  function formatDate(iso: string | null): string {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      // Localized date + time, no seconds. Stable across runs.
      return d.toLocaleString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      });
    } catch {
      return iso;
    }
  }

  function formatDimensions(w: number | null, h: number | null): string {
    if (w == null && h == null) return '—';
    return `${w ?? '?'} × ${h ?? '?'}`;
  }
</script>

<svelte:head>
  <title>{photo?.path ? photo.path.split('/').pop() ?? 'Photo' : 'Photo'}</title>
</svelte:head>

<main class="page">
  {#if loading}
    <div class="placeholder">Loading photo…</div>
  {:else if error}
    <div class="placeholder error">
      <p>{error}</p>
      <Button variant="ghost" href="/">Back to home</Button>
    </div>
  {:else if photo}
    <div class="layout">
      <!-- Photo column: large image with blurhash placeholder -->
      <section class="frame glass" aria-label="Photo viewer">
        {#if blurTint}
          <div
            class="blur"
            style:background-image="url({blurTint})"
            aria-hidden="true"
          ></div>
        {/if}
        <img
          src={photoUrl(photo.id, 1920)}
          alt={photo.path}
          class="hero"
        />
      </section>

      <!-- Sidebar: metadata + actions -->
      <aside class="sidebar glass" aria-label="Photo details">
        <!-- Identity / file path -->
        <header class="block">
          <h2 class="filename">{photo.path.split('/').pop() ?? photo.id}</h2>
          <button
            class="path"
            type="button"
            title="Click to copy path"
            onclick={() => copyText(photo!.path, 'File path')}
          >
            <span class="folder">{photo.folder ?? ''}/</span><span
              class="name"
              >{photo.path.split('/').pop() ?? ''}</span
            >
          </button>
        </header>

        <!-- Actions: like, dislike, similar, open raw -->
        <section class="actions" aria-label="Actions">
          <Button
            variant={photo.is_favorite ? 'primary' : 'secondary'}
            size="md"
            disabled={actionInFlight}
            onclick={toggleFavorite}
          >
            {photo.is_favorite ? 'Liked' : 'Like'}
          </Button>
          <Button
            variant="ghost"
            size="md"
            disabled={actionInFlight}
            onclick={onDislike}
          >
            Dislike
          </Button>
          <Button
            variant="ghost"
            size="md"
            href={`/similar/${encodeURIComponent(photo.id)}`}
          >
            Most similar
          </Button>
          <Button
            variant="ghost"
            size="md"
            href={photoUrl(photo.id)}
          >
            Open raw
          </Button>
        </section>

        <!-- Photo facts -->
        <section class="meta" aria-label="Photo metadata">
          <dl>
            <dt>Dimensions</dt>
            <dd>{formatDimensions(photo.width, photo.height)}</dd>

            <dt>Size</dt>
            <dd>{formatBytes(photo.size)}</dd>

            <dt>Indexed</dt>
            <dd>{formatDate(photo.indexed_at)}</dd>
          </dl>
        </section>

        <!-- Indexing / model info -->
        <section class="meta" aria-label="Indexing details">
          <h3>Indexed by</h3>
          <div class="chips">
            {#if photo.model_name}
              <Chip text={photo.model_name} title="Embedding model" />
            {/if}
            {#if photo.model_dim}
              <Chip
                text={`${photo.model_dim}-d`}
                title="Vector dimensionality"
              />
            {/if}
            {#if photo.collection}
              <Chip text={photo.collection} title="Collection" />
            {/if}
          </div>
          <dl>
            {#if photo.model_revision}
              <dt>Revision</dt>
              <dd class="mono">{photo.model_revision}</dd>
            {/if}
            <dt>ID</dt>
            <dd class="mono">{photo.id}</dd>
          </dl>
        </section>
      </aside>
    </div>
  {/if}
</main>

<style>
  .page {
    width: 100%;
    /* The TopBar is 64px; we want the photo + sidebar to fill the
       remaining vertical space. */
    min-height: calc(100vh - var(--topbar-height, 64px));
    padding: 24px;
    box-sizing: border-box;
  }

  .placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 16px;
    min-height: 50vh;
    color: var(--fg-2, #888);
  }
  .placeholder.error {
    color: var(--err, #c44);
  }

  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 360px;
    gap: 24px;
    align-items: start;
  }

  /* Single column on narrow screens — sidebar drops below the photo. */
  @media (max-width: 900px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }

  .frame {
    position: relative;
    overflow: hidden;
    border-radius: 12px;
    /* Cap the photo height so on huge displays it doesn't push the
       sidebar off-screen. The actual image fits inside this box. */
    max-height: calc(100vh - var(--topbar-height, 64px) - 48px);
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.4);
  }

  .blur {
    position: absolute;
    inset: 0;
    /* blurhash-bg.ts returns a CSS background string; this div
       paints the placeholder until the <img> loads over it. */
    filter: blur(20px);
    transform: scale(1.05); /* hide the blur edge */
  }

  .hero {
    position: relative;
    display: block;
    max-width: 100%;
    max-height: calc(100vh - var(--topbar-height, 64px) - 48px);
    object-fit: contain;
  }

  .sidebar {
    border-radius: 12px;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 24px;
    /* Allow long file paths to wrap instead of overflowing. */
    overflow-wrap: anywhere;
  }

  .block {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .filename {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 600;
    color: var(--fg, #eee);
    word-break: break-all;
  }

  .path {
    /* Looks like a path; acts like a button. */
    display: block;
    width: 100%;
    text-align: left;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 8px;
    padding: 8px 10px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.8rem;
    color: var(--fg-2, #aaa);
    cursor: pointer;
    transition:
      background 120ms ease,
      border-color 120ms ease;
  }
  .path:hover {
    background: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.12);
  }
  .path .folder {
    color: var(--fg-3, #777);
  }

  .actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .meta h3 {
    margin: 0 0 8px 0;
    font-size: 0.75rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--fg-3, #777);
  }

  .meta dl {
    margin: 0;
    display: grid;
    grid-template-columns: 90px 1fr;
    gap: 6px 12px;
    font-size: 0.85rem;
  }
  .meta dt {
    color: var(--fg-3, #777);
  }
  .meta dd {
    margin: 0;
    color: var(--fg, #eee);
  }
  .meta .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.78rem;
    word-break: break-all;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 12px;
  }
</style>