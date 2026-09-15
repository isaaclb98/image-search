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
   *     dislikePoint, undislikePoint, blurhash background)
   *   - $lib/components/Button (primary/secondary/ghost actions)
   *   - $lib/components/ActionButton (toggle actions — Like AND
   *     Dislike both expose aria-pressed indicating the current
   *     state, with mutual exclusivity between the two, matching
   *     the Lightbox)
   *   - $lib/components/Toaster (action feedback)
   *   - The existing TopBar from +layout.svelte
   *
   * What this page does NOT do:
   *   - Lightbox navigation (that's the click action on a tile)
   *   - Add-to-album picker (we link to the Lightbox for that)
   *   - In-place editing of any metadata (photos are immutable)
   *
   * Round‑31: the sidebar used to render indexing metadata
   * (Indexed date, Indexed‑by model / dim / collection,
   * Revision, ID). Those were implementation‑internal and not
   * useful to the user; removed. The page now only shows
   * Dimensions + Size.
   */
  import { onMount } from 'svelte';
  import type { PageData } from './$types';
  import {
    photoUrl,
    thumbUrl,
    likePoint,
    unlikePoint,
    dislikePoint,
    undislikePoint,
    addPhotoToAlbum,
    removePhotoFromAlbum,
    listAlbums,
    listAlbumsForFavorite
  } from '$lib/api/endpoints';
  import Button from '$lib/components/Button.svelte';
  import ActionButton from '$lib/components/ActionButton.svelte';
  import Dropdown from '$lib/components/Dropdown.svelte';
  import { toast } from '$lib/components/Toaster.svelte';
  import { blurhashToDataUrl } from '$lib/components/blurhash-bg';

  type PhotoMeta = {
    id: string;
    path: string;
    score: number;
    is_favorite: boolean;
    is_disliked: boolean;
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

  // Photo is pre-populated by the +page.ts universal load
  // function before this component renders. We keep it as a
  // $state so Like/Dislike toggles can mutate it in-place
  // (the optimistic UI); the load result is the seed.
  let { data }: { data: PageData } = $props();
  // The load function returns { photo: null, error: '...' }
  // when the API 404s or errors. The placeholder.error block
  // below handles that case; the rest of the component only
  // runs when `photo` is non-null.
  let photo = $state<PhotoMeta | null>(
    (data.photo as PhotoMeta | null) ?? null
  );
  let loading = $state(false);
  let errorMsg = $state<string | null>(data.error ?? null);
  let actionInFlight = $state(false);
  // Albums for the "Add to album" dropdown. Lazy-loaded on first
  // open so pages that never use the dropdown don't pay for the
  // fetch. Same pattern the Lightbox uses.
  let albums = $state<{ id: number; name: string }[] | null>(null);
  // Membership set: which of those albums the current photo is
  // already in. Loaded lazily on first dropdown open; refreshed
  // every time the dropdown opens so the indicator stays in
  // sync with the server.
  let memberOf = $state<Set<number>>(new Set());
  // Blurhash placeholder — async-decoded from the photo's blurhash
  // field after the metadata loads. Falls back to a dark surface
  // if decoding fails (no blurhash, malformed, etc.).
  let blurTint = $state<string | null>(null);

  // Decode the photo's blurhash for the local LQIP frame tint.
  // Round-37: this no longer writes to the global pageTint store —
  // the page-level backdrop is a flat dark base, and the per-photo
  // blurhash only paints behind the photo frame (the `.blur` div
  // inside `.frame`).
  onMount(() => {
    const data = photo;
    if (data?.blurhash) {
      blurhashToDataUrl(data.blurhash, 64, 40)
        .then((url) => {
          if (url && photo && photo.id === data.id) {
            blurTint = url;
          }
        })
        .catch(() => {
          /* leave blurTint null — the dark surface shows */
        });
    }
  });

  async function toggleFavorite() {
    if (!photo || actionInFlight) return;
    actionInFlight = true;
    const wasFav = photo.is_favorite;
    const wasDisliked = photo.is_disliked;
    // Optimistic toggle — the Lightbox does the same. Pressing Like
    // clears any active Dislike (mutual exclusivity: a photo can be
    // liked or disliked, not both). Mirrors Lightbox.toggleFavorite.
    photo = { ...photo, is_favorite: !wasFav, is_disliked: false };
    try {
      if (wasFav) {
        await unlikePoint(photo.id);
      } else {
        await likePoint(photo.id);
        // Lightbox only toggles the favourite flag; the server-side
        // dislike row (if any) was implicitly cleared by the like.
        // For the dedicated photo page we own the dislike endpoint
        // directly, so explicitly unmark so the row actually goes
        // away on the server too.
        if (wasDisliked) await undislikePoint(photo.id);
      }
    } catch {
      // Roll back on failure.
      photo = { ...photo, is_favorite: wasFav, is_disliked: wasDisliked };
      toast.show('Failed to update like.', { kind: 'error' });
    } finally {
      actionInFlight = false;
    }
  }

  async function toggleDislike() {
    if (!photo || actionInFlight) return;
    actionInFlight = true;
    const wasDisliked = photo.is_disliked;
    const wasFav = photo.is_favorite;
    // Optimistic toggle — the Lightbox does the same. Pressing
    // Dislike clears any active Like (mutual exclusivity). Mirrors
    // Lightbox.toggleDislike.
    photo = { ...photo, is_disliked: !wasDisliked, is_favorite: false };
    try {
      if (wasDisliked) {
        await undislikePoint(photo.id);
      } else {
        await dislikePoint(photo.id);
        // Explicitly clear the like server-side too (the Lightbox
        // relies on the parent to do this; here we own the endpoints
        // directly, so do it ourselves).
        if (wasFav) await unlikePoint(photo.id);
      }
    } catch {
      // Roll back on failure.
      photo = { ...photo, is_disliked: wasDisliked, is_favorite: wasFav };
      toast.show('Failed to dislike.', { kind: 'error' });
    } finally {
      actionInFlight = false;
    }
  }

  // Add-to-album handlers — same shape as Lightbox.addToAlbum.
  async function ensureAlbumsLoaded() {
    if (albums !== null) return;
    try {
      const res = (await listAlbums()) as { albums?: { id: number; name: string }[] };
      albums = res.albums ?? [];
    } catch {
      albums = [];
    }
  }

  async function addPhotoToAlbumAction(albumId: number, albumName: string) {
    if (!photo) return;
    const inThisAlbum = memberOf.has(albumId);
    // Optimistic toggle: flip the Set locally so the
    // checkmark updates instantly, then call the API. On
    // failure, restore the previous Set.
    const before = memberOf;
    const next = new Set(memberOf);
    if (inThisAlbum) next.delete(albumId);
    else next.add(albumId);
    memberOf = next;
    try {
      if (inThisAlbum) {
        await removePhotoFromAlbum(albumId, photo.id);
        toast.show(`Removed from "${albumName}"`, { kind: 'success' });
      } else {
        await addPhotoToAlbum(albumId, photo.id);
        toast.show(`Added to "${albumName}"`, { kind: 'success' });
      }
    } catch {
      memberOf = before;
      toast.show(`Could not update "${albumName}"`, { kind: 'error' });
    }
  }

  async function refreshMembership() {
    if (!photo) return;
    try {
      const albumsForPhoto = await listAlbumsForFavorite(photo.id);
      memberOf = new Set(albumsForPhoto.map((a) => a.id));
    } catch {
      // Leave the previous Set — a transient failure shouldn't
      // blank out the indicator.
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

  function formatDimensions(w: number | null, h: number | null): string {
    if (w == null && h == null) return '—';
    return `${w ?? '?'} × ${h ?? '?'}`;
  }
</script>

<svelte:head>
  <title>{photo?.path ? photo.path.split('/').pop() ?? 'Photo' : 'Photo'}</title>
</svelte:head>

<main class="page">
  {#if errorMsg}
    <div class="state error">
      <p>{errorMsg}</p>
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

        <!-- Actions: like, dislike, add to album, similar, open raw -->
        <section class="actions" aria-label="Actions">
          <ActionButton
            onclick={toggleFavorite}
            title="Like"
            ariaPressed={photo.is_favorite ? 'true' : 'false'}
          >
            Like
          </ActionButton>
          <ActionButton
            onclick={toggleDislike}
            title="Dislike"
            ariaPressed={photo.is_disliked ? 'true' : 'false'}
          >
            Dislike
          </ActionButton>
          <Dropdown
            items={(albums ?? []).map((a) => ({ id: a.id, label: a.name }))}
            onPick={async (it, _isMember) => {
              await addPhotoToAlbumAction(it.id as number, it.label);
            }}
            label="Add this photo to an album"
            align="up"
            emptyMessage="No albums yet — create one from the Albums page."
            memberOf={memberOf}
          >
            {#snippet trigger({ open, toggle })}
              <ActionButton
                onclick={async () => {
                  if (!open) {
                    await ensureAlbumsLoaded();
                    await refreshMembership();
                  }
                  toggle();
                }}
                title="Add this photo to an album"
                ariaHaspopup="menu"
                ariaExpanded={open}
              >
                Add to album
              </ActionButton>
            {/snippet}
          </Dropdown>
          <ActionButton
            href={`/similar/${encodeURIComponent(photo.id)}`}
            title="Open the dedicated most-similar page for this photo"
          >
            Most similar
          </ActionButton>
          <ActionButton
            href={photoUrl(photo.id)}
          >
            Open raw
          </ActionButton>
        </section>

        <!-- Photo facts -->
        <section class="meta" aria-label="Photo metadata">
          <dl>
            <dt>Dimensions</dt>
            <dd>{formatDimensions(photo.width, photo.height)}</dd>

            <dt>Size</dt>
            <dd>{formatBytes(photo.size)}</dd>
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
    padding: var(--s-4);
    box-sizing: border-box;
  }

  .placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--s-3);
    min-height: 50vh;
    color: var(--fg-2, #6b7280);
  }
  .placeholder.error {
    color: var(--negative, #ef4444);
  }

  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 360px;
    gap: var(--s-4);
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
      border-radius: var(--r-2);
      /* Fixed 3:2 container. The photo fits inside via
         object-fit: contain; the blurhash tint stretches behind it. */
      aspect-ratio: 3 / 2;
      /* Cap photo height on huge displays so it doesn't push the
         sidebar off-screen. When the cap binds, the ratio yields —
         the frame gets shorter but keeps the column width. */
      max-height: calc(100vh - var(--topbar-height, 64px) - var(--s-6));
      display: flex;
      align-items: center;
      justify-content: center;
      /* Round-38: was rgba(0,0,0,0.4) (dark fill behind photo on
         dark theme). On light theme, use a soft dark wash so the
         photo frame still reads as a contained surface against the
         mesh-gradient backdrop. */
      background: rgba(20, 22, 28, 0.06);
      border: 1px solid var(--glass-edge);
    }

  .blur {
    position: absolute;
    inset: 0;
    /* blurhash data URL is already a low-res smooth tint. Stretch
       it to fill the box (no tiling) so we don't get banding
       artifacts on portrait photos where the frame is much wider
       than the photo itself. No extra blur filter — it just
       makes the blurhash look muddy. (Round‑31 fix.) */
    background-size: cover;
    background-position: center;
  }

  .hero {
    position: relative;
    display: block;
    max-width: 100%;
    /* Frame owns the outer box (3:2 + viewport cap); the hero just
       fits inside it. object-fit: contain keeps the photo's native
       ratio — landscape photos letterbox vertically, portrait
       photos pillarbox horizontally, all over the blurhash tint. */
    max-height: 100%;
    object-fit: contain;
  }

  .sidebar {
    border-radius: var(--r-2);
    padding: var(--s-4);
    display: flex;
    flex-direction: column;
    gap: var(--s-4);
    /* Allow long file paths to wrap instead of overflowing. */
    overflow-wrap: anywhere;
  }

  .block {
    display: flex;
    flex-direction: column;
    gap: var(--s-1);
  }

  .filename {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 600;
    color: var(--fg-1, #1a1a1a);
    word-break: break-all;
  }

  .path {
    /* Looks like a path; acts like a button. */
    display: block;
    width: 100%;
    text-align: left;
    background: color-mix(in srgb, var(--fg-1) 4%, transparent);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-1);
    padding: var(--s-1) var(--s-2);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.8rem;
    color: var(--fg-2, #aaa);
    cursor: pointer;
    transition:
      background 120ms ease,
      border-color 120ms ease;
  }
  .path:hover {
    background: var(--glass-1);
    border-color: var(--glass-edge-strong);
  }
  .path .folder {
    color: var(--fg-3, #777);
  }

  .actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--s-1);
  }

  .meta h3 {
    margin: 0 0 var(--s-1) 0;
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
    gap: var(--s-1) var(--s-2);
    font-size: 0.85rem;
  }
  .meta dt {
    color: var(--fg-3, #777);
  }
  .meta dd {
    margin: 0;
    color: var(--fg-1, #1a1a1a);
  }
</style>