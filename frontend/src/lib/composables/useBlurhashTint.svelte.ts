/**
 * useBlurhashTint — extracts the Lightbox's blurhash backdrop
 * decode + crossfade logic. Owns:
 *   - the tint data URL (`tint`)
 *   - the crossfade gate (`tintReady`)
 *   - the session-freeze flag (`tintFrozen`)
 *
 * Behaviour preserved from Lightbox.svelte:
 *   - When the current photo has no blurhash, the tint is
 *     cleared and the freeze is reset (so the next
 *     lightbox session decodes a fresh tint).
 *   - When the tint is already frozen, the effect is a
 *     no-op (the backdrop stays calm for the duration of
 *     the session — round-32 UX).
 *   - Otherwise: drop tintReady immediately so the
 *     existing layer animates OUT, decode the new
 *     blurhash, then on the next animation frame flip
 *     tintReady + tintFrozen back on so the new layer
 *     animates IN. The rAF is essential — without it the
 *     browser composites the old class and the new bg in
 *     a single paint and skips the transition.
 *
 * Reactive deps: the current photo's blurhash. The
 * composable reads via getBlurhash() so the Lightbox's
 * items[idx] lookup stays in the call site.
 */
export type BlurhashTintOptions = {
  /** The current photo's blurhash, or null/undefined
   * if there is no current photo. */
  getBlurhash: () => string | null | undefined;
  /** Decode the blurhash to a CSS-ready data URL.
   * Lives at the call site so the composable doesn't
   * have to import the blurhash-bg module. Signature
   * matches the underlying blurhashToDataUrl helper
   * (which accepts null/undefined hash and returns
   * null when there's nothing to decode). */
  decode: (blurhash: string | null | undefined, w?: number, h?: number) => Promise<string | null>;
  /** Decode dimensions. The original Lightbox used 80x50
   * which gives a soft, low-cost backdrop. */
  decodeWidth?: number;
  decodeHeight?: number;
};

export type BlurhashTintController = {
  tint: string | null;
  tintReady: boolean;
  /** True after the first successful decode. Used by
   * the Lightbox to skip subsequent decodes so prev/next
   * doesn't churn the backdrop. */
  tintFrozen: boolean;
  /** Reset the freeze. The Lightbox calls this when
   * the lightbox closes (no current photo) so the
   * next open picks a fresh tint. */
  reset: () => void;
};

export function useBlurhashTint(
  opts: BlurhashTintOptions
): BlurhashTintController {
  let tint = $state<string | null>(null);
  let tintReady = $state(false);
  let tintFrozen = $state(false);

  $effect(() => {
    const blurhash = opts.getBlurhash();

    if (!blurhash) {
      // No current photo (lightbox closed or photo
      // has no blurhash). Clear and unfreeze so the
      // next session decodes a fresh tint.
      tint = null;
      tintReady = false;
      tintFrozen = false;
      return;
    }

    // Tint is already frozen (round-32 UX: stay calm
    // for the rest of the session).
    if (tintFrozen) return;

    // Crossfade: drop tintReady so the existing layer
    // animates OUT (opacity 0.65 → 0), decode the new
    // blurhash, then on the next animation frame flip
    // tintReady + tintFrozen back on so the new layer
    // animates IN.
    tintReady = false;
    opts
      .decode(blurhash, opts.decodeWidth ?? 80, opts.decodeHeight ?? 50)
      .then((u) => {
        if (!u) {
          // Decode failed (null hash, no canvas, etc.) —
          // clear the tint rather than render a broken
          // background-image: url(null).
          tint = null;
          tintReady = false;
          return;
        }
        tint = u;
        requestAnimationFrame(() => {
          tintReady = true;
          tintFrozen = true;
        });
      });
  });

  return {
    get tint() {
      return tint;
    },
    get tintReady() {
      return tintReady;
    },
    get tintFrozen() {
      return tintFrozen;
    },
    reset() {
      tint = null;
      tintReady = false;
      tintFrozen = false;
    }
  };
}
