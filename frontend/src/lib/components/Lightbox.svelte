<script lang="ts">
  /**
   * Lightbox — full-screen modal showing one image at a time.
   *   - ←/→ or A/D navigates prev/next
   *   - Esc or click outside closes
   *   - background blurs + tints from the current photo
   *   - "Most similar" navigates to /similar/{id} (closes itself)
   *
   * Slideshow: a Play/Pause button in the action bar starts
   * auto-advance from the currently-shown photo. The cadence
   * comes from the user's preference (see stores/preferences.ts,
   * surfaced on the Settings page) so it stays consistent across
   * sessions. Wrap-around is on while playing — Prev from the
   * first photo lands on the last — and off when paused so
   * manual nav respects the existing boundaries. Manual nav
   * (chevrons, ←/→, A/D) also pauses the timer, so a quick
   * back-step never has the photo "skip past" the user's click.
   * Press Space to toggle Play/Pause from anywhere in the
   * lightbox (unless a button is focused, in which case Space
   * activates that button naturally).
   *
   * Photo bytes come from /photo/{id}/raw?w=N — the server does a
   * Lanczos downsample and serves the cached JPEG. This avoids the
   * quality loss of letting the browser scale a 12 MP source down
   * to a 1408 px lightbox, and slashes bandwidth by ~10x.
   *
   * Caller provides the items array (with point IDs) and the
   * index of the currently shown item, plus a way to toggle
   * favourite.
   */
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { photoUrl, addPhotoToAlbum, removePhotoFromAlbum, listAlbums, listAlbumsForFavorite } from '$lib/api/endpoints';
  import { preferences } from '$lib/stores/preferences';
  import Icon from './Icon.svelte';
  import { blurhashToDataUrl } from './blurhash-bg';
  import ActionButton from './ActionButton.svelte';
  import Dropdown from './Dropdown.svelte';
  import { toast } from './Toaster.svelte';

  function goSimilar(id: string) {
    onClose();
    goto(`/similar/${encodeURIComponent(id)}`);
  }

  /**
   * Pick the right server-side resize width. We aim for 2x of the
   * rendered CSS width (retina) but cap at 1920 so 4K monitors
   * don't pull multi-MB files when 1920 px is enough visually.
   * Falls back to 1920 in SSR (window not available).
   */
  function lightboxWidth(): number {
    if (typeof window === 'undefined') return 1920;
    const cssWidth = window.innerWidth - 32;
    const dpr = Math.max(1, window.devicePixelRatio || 1);
    return Math.min(1920, Math.ceil(cssWidth * dpr));
  }

  type Item = {
    id: string;
    blurhash?: string | null;
    isFavorite?: boolean;
    isDisliked?: boolean;
  };

  type Props = {
    items: Item[];
    index: number;
    onClose: () => void;
    onToggleFavorite?: (id: string) => void;
    onDislike?: (id: string) => void;
    /** User-created albums — passed down to the right-click
     *  "Add to album" submenu (round-5). */
    albums?: { id: number; name: string }[];
  };
  let {
    items,
    index,
    onClose,
    onToggleFavorite,
    onDislike,
    albums
  }: Props = $props();

  let idx = $state(index);
  // Clamp `idx` to a valid range only when it's actually out of
  // bounds (e.g., items shrunk). Don't reset it on every items
  // update — earlier a `$effect(() => idx = clamp(index, ...))`
  // would fire mid-render with a transient items array and send
  // idx to 0, which made the Like click jump the user back to
  // the first photo (issue round-4 #1).
  $effect(() => {
    if (items.length > 0 && idx >= items.length) {
      idx = items.length - 1;
    }
  });

  let tint = $state<string | null>(null);
  // Crossfade gate for the .tint backdrop layer. See the template
  // note above for why this needs to be rAF-deferred — without
  // the gate the browser batches the style change and the
  // transition never fires.
  let tintReady = $state(false);
  // Round-32: freeze the tint after the first decode of the
  // lightbox session. Without this, every prev/next navigation
  // re-decodes the photo's blurhash and the backdrop churns
  // (felt as the tint jumping wildly between photos). Reset
  // when the lightbox closes so the next open picks a fresh
  // tint from the new first photo.
  let tintFrozen = $state(false);

  // Photo-load state for crossfade. The lightbox used to flash on
  // every navigation because the `<img>` was inside `{#key it.id}`,
  // which unmounted the old image and mounted a new one — the dark
  // backdrop showed through until the new fetch resolved. Now we
  // reuse the same `<img>` element across navigations (its `src`
  // updates reactively when `idx` changes) and gate its visibility
  // on `naturalWidth > 0` via a CSS transition. While the new src
  // is loading, the blurhash backdrop (`tint`) stays visible so
  // there's never a fully-blank frame.
  //
  // `photoReady` flips to false whenever the displayed src changes
  // and back to true when the next `load` event lands. We track the
  // src we last saw so a duplicate `load` event for the same photo
  // (browsers fire these on cache hits too) doesn't toggle state
  // twice.
  let photoReady = $state(false);
  let lastLoadedSrc = $state('');
  let photoSrc = $state('');

  // Adjacent-photo preloader. Hidden `<img>` elements whose `src`
  // is set to the next/previous photo's URL. The browser shares
  // the HTTP cache with the visible `<img>` and reuses the
  // decoded bitmap on navigation, so prev/next feel instant.
  //
  // Why a Set instead of just rendering all of `items.slice(idx-2,
  // idx+3)`: preloading the same URL twice would create two <img>
  // nodes holding the same data — wasteful when the user lingers
  // on a photo and `idx` stays put. The Set ensures we only mount
  // each preloader once; subsequent effect runs (caused by
  // blurhash decode, memberOf refresh, etc.) are no-ops.
  //
  // Distance: ±1 by default. The 1s slideshow preset only gives
  // the user 1000 ms between ticks — `playing` bumps that to ±3
  // so the auto-advance doesn't race the network. Single-photo
  // lightbox (`items.length <= 1`) skips preloading entirely.
  //
  // Bandwidth respect: skip preloading when the user has
  // declared save-data via the Network Information API or the
  // `prefers-reduced-data` media query. Both are
  // feature-detected; old browsers that lack them get the
  // default ±1 preload (a strict-power-user setting, but that's
  // the conservative default for a desktop-class lightbox).
  let preloadedSrcs = $state<Set<string>>(new Set());

  /* Local "pressed" state for the Like / Dislike action buttons.
     Synced from the current photo only when the user navigates
     to a different photo — re-syncing after every server response
     would clobber the in‑flight toggle we just made. Pressing
     Like clears Dislike (and vice‑versa) so a photo can only be
     in one of those two states at a time. The parent (grid) still
     owns the API call. */
  let isFavorite = $state(false);
  let isDisliked = $state(false);
  let lastSyncedIdx = $state(-1);

  $effect(() => {
    if (idx === lastSyncedIdx) return;
    const it = items[idx];
    isFavorite = !!it?.isFavorite;
    isDisliked = !!it?.isDisliked;
    lastSyncedIdx = idx;
  });

  function toggleFavorite() {
    const it = current();
    if (!it) return;
    if (isFavorite) {
      isFavorite = false;
    } else {
      isFavorite = true;
      isDisliked = false;
    }
    onToggleFavorite?.(it.id);
  }

  function toggleDislike() {
    const it = current();
    if (!it) return;
    if (isDisliked) {
      isDisliked = false;
    } else {
      isDisliked = true;
      isFavorite = false;
    }
    onDislike?.(it.id);
  }

  // Round-6 — Add to album dropdown logic. Now lives in <Dropdown>;
  // this handler is just the API glue + lazy album loading.
  //
  // The Dropdown shows membership state via the `memberOf` Set,
  // and `onPick` carries the isMember boolean so we can decide
  // add-vs-remove. Both the photo page and the right-click menu
  // use the same pattern.
  async function ensureAlbumsLoaded() {
    if (albums && albums.length > 0) return;
    try {
      const res = (await listAlbums()) as { albums?: { id: number; name: string }[] };
      albums = res.albums ?? [];
    } catch {
      albums = [];
    }
  }

  // Membership set for the current photo: which albums it
  // already belongs to. Loaded lazily when the user opens the
  // dropdown; reloaded after every add/remove so the visual
  // indicator stays in sync with the server.
  let memberOf = $state<Set<number>>(new Set());
  let membershipLoading = $state(false);

  async function refreshMembership() {
    const it = current();
    if (!it) return;
    membershipLoading = true;
    try {
      const albumsForPhoto = await listAlbumsForFavorite(it.id);
      memberOf = new Set(albumsForPhoto.map((a) => a.id));
    } catch {
      // Leave the previous Set in place — a transient failure
      // shouldn't blank out the indicator.
    } finally {
      membershipLoading = false;
    }
  }

  async function toggleMembership(albumId: number, albumName: string) {
    const it = current();
    if (!it) return;
    const inThisAlbum = memberOf.has(albumId);
    // Optimistic toggle: flip the Set locally first so the
    // checkmark updates instantly, then call the API. On
    // failure, restore the previous Set.
    const before = memberOf;
    const next = new Set(memberOf);
    if (inThisAlbum) next.delete(albumId);
    else next.add(albumId);
    memberOf = next;
    try {
      if (inThisAlbum) {
        await removePhotoFromAlbum(albumId, it.id);
        toast.show(`Removed from "${albumName}"`, { kind: 'success' });
      } else {
        await addPhotoToAlbum(albumId, it.id);
        toast.show(`Added to "${albumName}"`, { kind: 'success' });
      }
    } catch {
      memberOf = before;
      toast.show(`Could not update "${albumName}"`, { kind: 'error' });
    }
  }

  function current(): Item | null {
    return items[idx] ?? null;
  }

  /* Slideshow state. The Play/Pause button in the action bar is
     always present when there's more than one photo to cycle
     through, and the timer starts idle — the user has to opt in
     by pressing Play. Auto-play on open would be jarring: the
     lightbox pops in, then 4 seconds later starts moving on its
     own with no visual indicator until the timer first fires.
     Explicit Play also sidesteps prefers-reduced-motion entirely
     (the user who cares about motion sensitivity just won't hit
     the button). `pausedByUser` tracks whether the user has
     explicitly paused vs. the timer just being unstarted, which
     is currently only used to keep the button label honest when
     items shrink to a single entry mid-playback. */
  let playing = $state(false);
  let pausedByUser = $state(false);

  // Stop the timer if there's nothing to advance through (single
  // item or empty). Reactive on `items.length` so a re-load that
  // shrinks the set gracefully settles into "no auto-advance".
  $effect(() => {
    if (items.length <= 1) playing = false;
  });

  // Auto-advance. Effect re-runs whenever `playing` flips or the
  // cadence changes, so toggling Play/Pause restarts cleanly and
  // the user can change the interval from Settings mid-playback
  // (next tick picks it up). `idx` reads inside the tick because
  // each fire advances the index; we don't want `idx` itself to
  // be a dependency (that would re-create the interval every
  // photo and never advance).
  $effect(() => {
    if (!playing) return;
    const intervalMs = $preferences.slideshowIntervalMs;
    const id = setInterval(() => {
      if (items.length === 0) return;
      idx = (idx + 1) % items.length;
    }, intervalMs);
    return () => clearInterval(id);
  });

  function togglePlay() {
    playing = !playing;
    pausedByUser = !playing;
  }

  // Re-run the photo-dependent effects whenever the current photo
  // changes (lightbox navigation, photo prop change). Tint is the
  // ambient background blur; memberOf is the add-to-album
  // membership indicator on the dropdown.
  $effect(() => {
    const it = current();
    // Compute the next src. `lightboxWidth()` is called fresh so
    // a window resize between navigations picks up the new width
    // without us needing a separate resize listener.
    const nextSrc = it ? photoUrl(it.id, lightboxWidth()) : '';
    if (nextSrc !== photoSrc) {
      photoSrc = nextSrc;
      // Mark the photo as not-ready until the new `<img>` fires
      // its `load` event. If the browser serves this from cache
      // the load fires synchronously-ish, but the $effect below
      // still sees it and flips photoReady back on.
      photoReady = false;
    }
    if (!it || !it.blurhash) {
      tint = null;
      tintReady = false;
      // Reset the freeze too so the next lightbox session decodes
      // the first photo of that session (round-32 UX: tint frozen
      // for the session so prev/next doesn't churn the backdrop).
      tintFrozen = false;
      return;
    }
    // Freeze the tint after the first decode of the session.
    // Navigating prev/next keeps the original photo's tint so the
    // backdrop stays calm — a smooth crossfade on lightbox open,
    // then stable for the duration of the session. The flag resets
    // above when the lightbox closes (no photo) so the next open
    // picks a fresh tint from the new first photo.
    if (tintFrozen) return;
    // Crossfade the backdrop. Drop tintReady immediately so the
    // existing layer animates OUT (opacity 0.65 → 0), then on the
    // next animation frame after the new blurhash resolves, flip
    // tintReady back to true so the new layer animates IN.
    //
    // Without the rAF we get a hard cut: Svelte updates the inline
    // `background-image` style synchronously, and the browser
    // composites both the old class and the new bg in a single
    // paint, skipping the transition entirely. The rAF forces a
    // paint at opacity 0 first so the next state change has a
    // transition to run.
    tintReady = false;
    blurhashToDataUrl(it.blurhash, 80, 50).then((u) => {
      tint = u;
      requestAnimationFrame(() => {
        tintReady = true;
        tintFrozen = true;
      });
    });
    // Refresh the membership indicator for the new photo. Done
    // in the same effect so a single `idx` change triggers both
    // (one round trip, no flicker between the two updates).
    void refreshMembership();
  });

  // Adjacent-photo preloader. Runs on every `idx` change so the
  // preload window tracks the cursor. Reactive deps: `idx` (the
  // cursor) and `playing` (window expands during slideshow). We
  // deliberately do NOT track `photoUrl`'s width argument —
  // `lightboxWidth()` reads `window.innerWidth`, which is reactive
  // in Svelte 5 only via the `if (browser)` guard, so a resize
  // mid-lightbox may briefly leave a stale preload at the old
  // width until the next navigation; that's fine — the cache
  // entry is shared across widths via the `?w=...` query.
  $effect(() => {
    // Touch the deps so Svelte tracks them.
    const cursor = idx;
    const isPlaying = playing;
    void items.length;

    // Skip on single-photo lightbox or when the user opted into
    // data-saver mode. `saveData` is the Network Information API
    // flag; `prefers-reduced-data: reduce` is the OS-level setting
    // we honour via matchMedia. Both are optional — old browsers
    // simply lack them and we fall back to the default preload.
    if (items.length <= 1) {
      // Evict any leftover preload entries from a previous
      // multi-photo session. The Set is the source of truth that
      // the hidden <img> nodes mirror, so clearing it tells the
      // template to remove them on the next render.
      if (preloadedSrcs.size > 0) preloadedSrcs = new Set();
      return;
    }
    const conn =
      typeof navigator !== 'undefined'
        ? (navigator as Navigator & {
            connection?: { saveData?: boolean };
          }).connection
        : undefined;
    const saveData =
      !!conn?.saveData ||
      (typeof window !== 'undefined' &&
        !!window.matchMedia?.('(prefers-reduced-data: reduce)').matches);
    if (saveData) {
      if (preloadedSrcs.size > 0) preloadedSrcs = new Set();
      return;
    }

    // Distance: ±3 during slideshow so a 1s cadence doesn't
    // race the network; ±1 otherwise.
    const distance = isPlaying ? 3 : 1;
    const want = new Set<string>();
    for (let d = 1; d <= distance; d++) {
      // Wrap-around only while the slideshow is running —
      // manual prev at the first photo and next at the last
      // are intentionally disabled, so preloading the wrap
      // would be wasted bandwidth.
      if (isPlaying) {
        const nextIdx = (cursor + d) % items.length;
        const prevIdx = (cursor - d + items.length) % items.length;
        const nextIt = items[nextIdx];
        const prevIt = items[prevIdx];
        if (nextIt) want.add(photoUrl(nextIt.id, lightboxWidth()));
        if (prevIt) want.add(photoUrl(prevIt.id, lightboxWidth()));
      } else {
        const nextIt = items[cursor + d];
        const prevIt = items[cursor - d];
        if (nextIt) want.add(photoUrl(nextIt.id, lightboxWidth()));
        if (prevIt) want.add(photoUrl(prevIt.id, lightboxWidth()));
      }
    }

    // Evict entries that fell out of the window. Without this,
    // a fast scrub through a 1000-photo grid would mount 1000
    // hidden <img> nodes; the browser would keep them all
    // cached and we'd hold 1000 decoded bitmaps in memory.
    let changed = false;
    const next = new Set<string>();
    for (const src of want) {
      if (preloadedSrcs.has(src) || src === photoSrc) next.add(src);
      else {
        next.add(src);
        changed = true;
      }
    }
    for (const src of preloadedSrcs) {
      if (!next.has(src)) changed = true;
    }
    if (changed) preloadedSrcs = next;
  });

  function prev() {
    // Snapshot the playing flag before mutating it. We want
    // wrap-around in two cases: (a) the slideshow is currently
    // running, and (b) the user just paused it via this very
    // click — they're still mid-slideshow mentally, so looping
    // to the previous photo is the intuitive continuation.
    // Outside that window, respect the pre-slideshow boundary
    // (prev disabled at the first photo).
    const wasPlaying = playing;
    if (playing) playing = false;
    if (!wasPlaying) {
      if (idx > 0) idx -= 1;
      return;
    }
    if (items.length === 0) return;
    idx = (idx - 1 + items.length) % items.length;
  }
  function next() {
    const wasPlaying = playing;
    if (playing) playing = false;
    if (!wasPlaying) {
      if (idx < items.length - 1) idx += 1;
      return;
    }
    if (items.length === 0) return;
    idx = (idx + 1) % items.length;
  }

  function onKey(e: KeyboardEvent) {
    // ArrowLeft/ArrowRight and Escape always run, regardless of
    // what's focused — they're navigation, not activation.
    if (e.key === 'Escape') onClose();
    else if (e.key === 'ArrowLeft' || e.key.toLowerCase() === 'a') prev();
    else if (e.key === 'ArrowRight' || e.key.toLowerCase() === 'd') next();
    else if (e.key === 'Home') {
      // Jump to first photo. Same button-focus caveat as Space —
      // don't fight the browser's synthetic click on a focused
      // button.
      if (e.target instanceof HTMLElement && e.target.tagName === 'BUTTON') return;
      if (items.length > 0) {
        e.preventDefault();
        idx = 0;
      }
    } else if (e.key === 'End') {
      if (e.target instanceof HTMLElement && e.target.tagName === 'BUTTON') return;
      if (items.length > 0) {
        e.preventDefault();
        idx = items.length - 1;
      }
    } else if (e.key === 'PageUp') {
      // Skip back 10 — large-grid navigation. Bound to [0, N-1].
      if (e.target instanceof HTMLElement && e.target.tagName === 'BUTTON') return;
      if (items.length > 0) {
        e.preventDefault();
        idx = Math.max(0, idx - 10);
      }
    } else if (e.key === 'PageDown') {
      if (e.target instanceof HTMLElement && e.target.tagName === 'BUTTON') return;
      if (items.length > 0) {
        e.preventDefault();
        idx = Math.min(items.length - 1, idx + 10);
      }
    } else if (e.key === 'f' || e.key === 'F') {
      // Like / Unlike shortcut. Disabled while typing in a
      // text field so we don't steal keys from the rename
      // input on the Add-to-album dialog, etc.
      if (e.target instanceof HTMLElement) {
        const tag = e.target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      }
      e.preventDefault();
      toggleFavorite();
    } else if (e.key === 'd' || e.key === 'D') {
      // Wait — 'd' is already used for Next (alongside ArrowRight).
      // Reuse the same key, but only when Shift is NOT held —
      // Shift+D is a different gesture and shouldn't fire dislike.
      // Actually, looking at the existing handler: 'd' (no Shift)
      // calls next(). So we can't double-up on plain 'd' for
      // dislike without breaking the existing shortcut. Users
      // who want a keyboard shortcut for dislike can use the
      // right-click context menu or the on-screen button.
      // (Skip — see comment below.)
    } else if (e.key === ' ' || e.code === 'Space') {
      // Space natively activates focused buttons. If we also
      // fire togglePlay() here, the synthetic click from the
      // button + our handler would net out to no-op and the
      // timer would never start. So skip our handler when a
      // button is focused and let the browser fire the click
      // naturally. ArrowLeft/Right don't have this issue since
      // browsers don't fire synthetic clicks for them on
      // buttons.
      if (e.target instanceof HTMLElement && e.target.tagName === 'BUTTON') {
        return;
      }
      // Toggle the slideshow. Only meaningful when there's more
      // than one photo to cycle through (the same gate the
      // button uses to render itself). preventDefault stops
      // Space from also scrolling the page underneath the
      // lightbox — body overflow is hidden anyway, but
      // belt-and-braces against future layout changes.
      if (items.length >= 2) {
        e.preventDefault();
        togglePlay();
      }
    }
  }
  onMount(() => {
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Hide the top tab bar while the lightbox is open — otherwise
    // its z-50 sticky strip bleeds through the semi-transparent
    // overlay.
    document.body.classList.add('lightbox-open');
    return () => {
      document.body.style.overflow = prevOverflow;
      document.body.classList.remove('lightbox-open');
    };
  });
</script>

<svelte:window onkeydown={onKey} />

<div
  class="overlay"
  style={tint ? `--glass-tint: url(${tint})` : undefined}
  onclick={onClose}
  role="dialog"
  aria-modal="true"
>
  {#if tint}
    <!-- Crossfade the tint layer when its background-image swaps.
         The opacity transition runs on the same axis as the photo
         crossfade (both 150 ms), so the user perceives a single
         coordinated swap rather than two distinct layer changes.
         `.tint-ready` is set on the next animation frame after
         the bg-url changes — without the rAF gate, the browser
         would batch the style change and never run the transition
         (it has to first paint the old URL at full opacity, then
         paint the new one and animate down/up). -->
    <div
      class="tint"
      class:tint-ready={tintReady}
      style:background-image="var(--glass-tint)"
      aria-hidden="true"
    ></div>
  {/if}
  <div class="content" onclick={(e) => e.stopPropagation()} oncontextmenu={(e) => e.preventDefault()}>
    <button class="nav close" type="button" onclick={onClose} aria-label="Close">
      <Icon name="close" size={20} />
    </button>
    <button
      class="nav prev"
      type="button"
      onclick={prev}
      disabled={!playing && idx === 0}
      aria-label="Previous"
    >
      <Icon name="chevron-left" size={22} />
    </button>
    {#if current()}
      {@const it = current()!}
      <!-- Single reusable <img>; src updates reactively when idx
           changes. Crossfade via .photo { opacity } tied to
           photoReady so the new image only fades in once the
           browser has decoded it. No {#key} — tearing down the
           element on every nav caused the old flash because the
           browser would briefly render the dark backdrop until
           the new fetch resolved. -->
      <img
        class="photo"
        class:photo-ready={photoReady}
        src={photoSrc}
        alt=""
        onload={() => {
          // Guard against cache-hit replays for the same src.
          // The browser may also fire `load` after the src has
          // already changed to a different photo; we only flip
          // photoReady when the loaded src matches what we're
          // currently showing.
          if (photoSrc && photoSrc === lastLoadedSrc) return;
          lastLoadedSrc = photoSrc;
          photoReady = true;
        }}
      />
    {/if}
    <!-- Adjacent-photo preloaders. Hidden, off-screen, no
         interaction — they exist only so the browser's image
         cache + decoded bitmap are warm when the user
         navigates. `fetchpriority="low"` keeps them from
         competing with the visible <img>; `loading="eager"`
         overrides the default lazy-loading because we
         explicitly want the fetch now (not "when the
         IntersectionObserver notices this offscreen node").
         The Set is the source of truth — additions/removals
         drive mount/unmount. aria-hidden + tabindex=-1 so
         screen readers and keyboard focus skip them. -->
    {#each [...preloadedSrcs] as preloadSrc (preloadSrc)}
      <img
        class="preload"
        aria-hidden="true"
        tabindex="-1"
        alt=""
        src={preloadSrc}
        loading="eager"
        decoding="async"
        fetchpriority="low"
      />
    {/each}
    <button
      class="nav next"
      type="button"
      onclick={next}
      disabled={!playing && idx === items.length - 1}
      aria-label="Next"
    >
      <Icon name="chevron-right" size={22} />
    </button>
  </div>

  <div class="bar glass-strong" onclick={(e) => e.stopPropagation()} oncontextmenu={(e) => e.preventDefault()}>
      <span class="count">{idx + 1} / {items.length}</span>
      {#if items.length >= 2}
        <ActionButton
          onclick={togglePlay}
          title={playing ? 'Pause slideshow (Space)' : 'Play slideshow (Space)'}
          ariaPressed={playing ? 'true' : 'false'}
        >
          <Icon name={playing ? 'pause' : 'play'} size={14} />
        </ActionButton>
      {/if}
      <ActionButton
        onclick={toggleFavorite}
        title="Like (F)"
        ariaPressed={isFavorite ? 'true' : 'false'}
      >
        Like
      </ActionButton>
      <ActionButton
        onclick={toggleDislike}
        title="Dislike"
        ariaPressed={isDisliked ? 'true' : 'false'}
      >
        Dislike
      </ActionButton>
      <ActionButton
        onclick={() => current() && goSimilar(current()!.id)}
        title="Open the dedicated most-similar page for this photo"
      >
        Most similar
      </ActionButton>
      <Dropdown
        items={(albums ?? []).map((a) => ({ id: a.id, label: a.name }))}
        onPick={async (it, isMember) => {
          await toggleMembership(it.id as number, it.label);
          // `isMember` is now passed for callers that want to
          // branch on it, but toggleMembership already read the
          // canonical `memberOf` Set before the click so it
          // always flips the right way.
          void isMember;
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
        href={current() ? photoUrl(current()!.id) : '#'}
        target="_blank"
        rel="noopener"
      >Open raw</ActionButton>
    </div>
</div>

<style>
  .overlay {
    position: fixed;
    inset: 0;
    /* Above the top bar (z-50) and every other layer. */
    z-index: 500;
    /* Lightbox overlay uses the heavy glass tier (strongest
       frost) because the photo behind the action bar is
       the highest-contrast content in the app — the bar
       needs every bit of frost to stay legible. */
    background: rgba(8,8,12, var(--glass-alpha-scrim));
    backdrop-filter: var(--glass-heavy);
    -webkit-backdrop-filter: var(--glass-heavy);
    /* Two stacked rows: the image region (content) and the action
       bar. The row-gap is the sole source of spacing between
       them — .content has no border or margin contributing extra
       space. */
    display: grid;
    grid-template-rows: 1fr auto;
    row-gap: 16px;
    padding: 16px;
    box-sizing: border-box;
    animation: fade var(--t-med) var(--ease-out);
  }
  /* When the lightbox is open, the top tab bar would otherwise
     bleed through the semi-transparent overlay (rgba 0.55). Hide
     it via a body class so the user isn't fighting two layers of
     nav at once. */
  :global(body.lightbox-open .topbar) {
    display: none;
  }
  @keyframes fade {
    from { opacity: 0; }
    to   { opacity: 1; }
  }
.tint {
    position: absolute;
    inset: -40px;
    /* background-image is set via inline style from the JS blurhash
       decode; the rest of the shorthand stays here so the inline
       style only has to ship the data-URL, not repeat the layout
       commands. Default opacity 0 forces the layer to animate in
       when `.tint-ready` is applied (see the template note above
       for why the rAF gate matters). */
    background-repeat: no-repeat;
    background-position: center;
    background-size: cover;
    filter: blur(60px) saturate(1.5) brightness(0.55);
    opacity: 0;
    transition: opacity 150ms var(--ease-out);
    pointer-events: none;
    z-index: -1;
  }
  .tint.tint-ready {
    opacity: 0.65;
  }
  .content {
    /* Fill the first (1fr) row of the overlay grid. The grid
       already gives it the vertical space left after the action
       bar, so we just need width/height 100% inside that row. */
    position: relative;
    /* Include the 1px border inside the cell's width/height so
       100% on .photo never overflows the overlay. */
    box-sizing: border-box;
    width: 100%;
    height: 100%;
    display: grid;
    place-items: center;
    border-radius: var(--r-3);
    overflow: hidden;
    background: rgba(8,8,12,0.4);
    border: 1px solid var(--glass-edge);
  }
  .photo {
    display: block;
    /* Fill the entire photo cell above the action bar while
       preserving the image's natural aspect ratio. Absolute
       positioning + inset 0 is the most reliable way to size a
       replaced element (img) against a grid cell without being
       overridden by its intrinsic dimensions. object-fit: contain
       then scales the rendered image up or down to fit inside
       that box — small thumbnails get enlarged, large ones get
       shrunk, nothing crops and nothing overflows. */
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: contain;
    border-radius: var(--r-2);
    box-shadow: var(--shadow-3);
    /* Crossfade between photos. Default opacity 0 keeps the dark
       backdrop visible until the <img> fires its `load` event,
       at which point .photo-ready is applied and the transition
       fades the new image in over ~150 ms. The old `{#key}`
       approach unmounted and remounted the element on every nav,
       so the user briefly saw the bare backdrop — this is the
       fix. Tuned to 150 ms: long enough to mask the decode
       jank, short enough that fast slideshow ticks don't smear
       into a blur. The transition is opacity-only so the
       gallery geometry stays stable across swaps. */
    opacity: 0;
    transition: opacity 150ms var(--ease-out);
  }
  .photo.photo-ready {
    opacity: 1;
  }
  /* Adjacent-photo preloaders. Visually inert — the browser
     still renders the image into its image cache, but we hide
     the element from the user. Zero-size off-screen positioning
     is the lightest way to do this: `display: none` would
     cancel the fetch in some browsers, `visibility: hidden`
     leaves the decoded bitmap off the compositor. `position:
     fixed` with a far-offscreen top keeps them in the layout
     tree (so the browser considers them "visible enough" to
     fetch + decode) but out of any viewport. */
  .preload {
    position: fixed;
    top: -10000px;
    left: -10000px;
    width: 1px;
    height: 1px;
    opacity: 0;
    pointer-events: none;
  }
  .nav {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    width: 48px;
    height: 48px;
    border-radius: 50%;
    background: rgba(14,15,20,0.65);
    border: 1px solid var(--glass-edge-strong);
    color: var(--fg-1);
    font-size: 26px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: background var(--t-fast);
    z-index: 1;
  }
  .nav:hover { background: rgba(14,15,20,0.85); }
  .nav:disabled { opacity: 0.3; cursor: not-allowed; }
  .prev { left: 12px; }
  .next { right: 12px; }
  .close {
    top: 12px;
    right: 12px;
    transform: none;
    font-size: 22px;
  }
  .bar {
    /* Grid item in the overlay's second row. justify-self + align-self
       centre the pill horizontally and vertically inside that row,
       so the action bar is always centred between the dialog's left
       and right edges regardless of viewport width. */
    justify-self: center;
    align-self: center;
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 8px 14px;
    border-radius: var(--r-pill);
    box-shadow: var(--shadow-2);
  }
  .count {
    color: var(--fg-2);
    font-size: var(--fs-sm);
    padding: 0 6px;
    /* Same rationale as .action: never wrap "1" and "/ 20" onto
       separate lines when the bar is tight. */
    white-space: nowrap;
    flex-shrink: 0;
  }

</style>
