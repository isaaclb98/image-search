/**
 * useSlideshow — extracts the auto-advance timer logic from
 * the Lightbox. Returns { playing, togglePlay }.
 *
 * Behaviour preserved verbatim from Lightbox.svelte:
 *   - `playing` starts false (the user has to opt in).
 *   - Reactive on `items.length`: a re-load that shrinks
 *     the set settles into "no auto-advance".
 *   - The interval is created when `playing` flips on (or
 *     when the cadence changes mid-playback — the next tick
 *     picks up the new interval).
 *   - `idx` is read inside the tick (not subscribed to) so
 *     the effect doesn't re-create the interval every photo.
 *   - Wrap-around: idx = (idx + 1) % items.length.
 *
 * The composable does not own `idx` — the caller passes a
 * getter + setter so the existing Lightbox state stays the
 * single source of truth.
 */
export type SlideshowController = {
  playing: boolean;
  togglePlay: () => void;
  /** Stop the slideshow (no-op if already paused). Used
   * by the Lightbox's prev/next so user navigation
   * always pauses an in-progress slideshow. */
  pause: () => void;
};

export type SlideshowOptions = {
  /** How many photos are in the lightbox. Reactive. */
  getItemsLength: () => number;
  /** Read the current index. Called once per tick. */
  getIdx: () => number;
  /** Write the next index. */
  setIdx: (next: number) => void;
  /** Reactive cadence in ms. The composable re-reads
   * this on every effect re-run, so the user can change
   * the interval from Settings mid-playback and the next
   * tick picks it up. */
  getIntervalMs: () => number;
};

export function useSlideshow(opts: SlideshowOptions): SlideshowController {
  let playing = $state(false);

  // Stop the timer if there's nothing to advance through
  // (single item or empty). Reactive on items.length so a
  // re-load that shrinks the set gracefully settles into
  // "no auto-advance".
  $effect(() => {
    if (opts.getItemsLength() <= 1) playing = false;
  });

  // Auto-advance. Effect re-runs whenever `playing` flips
  // or the cadence changes. `idx` is read inside the tick
  // because each fire advances the index; we don't want
  // `idx` itself to be a dependency (that would re-create
  // the interval every photo and never advance).
  $effect(() => {
    if (!playing) return;
    const intervalMs = opts.getIntervalMs();
    const id = setInterval(() => {
      const len = opts.getItemsLength();
      if (len === 0) return;
      opts.setIdx((opts.getIdx() + 1) % len);
    }, intervalMs);
    return () => clearInterval(id);
  });

  function togglePlay() {
    playing = !playing;
  }

  return {
    get playing() {
      return playing;
    },
    togglePlay,
    pause: () => {
      playing = false;
    }
  };
}
