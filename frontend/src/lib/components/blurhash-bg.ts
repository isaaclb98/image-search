/**
 * Tiny blurhash decoder for LQIP tinting.
 *
 * Decode a blurhash to a small data URL we can drop in as a
 * background behind a glass surface. Keeps the bundle small —
 * the heavy work happens on demand.
 *
 * If anything fails (no blurhash, malformed, browser doesn't
 * support canvas), returns null so the caller falls through.
 */

import { pageTint } from '$lib/stores/tint';

let _mod: Promise<typeof import('blurhash')> | null = null;
async function load() {
  if (!_mod) _mod = import('blurhash');
  return _mod;
}

export async function blurhashToDataUrl(
  hash: string | null | undefined,
  w = 32,
  h = 32
): Promise<string | null> {
  if (!hash) return null;
  try {
    const bh = await load();
    const pixels = bh.decode(hash, w, h, 1);
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const img = ctx.createImageData(w, h);
    img.data.set(pixels);
    ctx.putImageData(img, 0, 0);
    return canvas.toDataURL();
  } catch {
    return null;
  }
}

/**
 * Round‑31: push a colour tint to the page so non-PhotoGrid
 * pages (e.g. /albums) also get the glassy backdrop. Fetches
 * a single random photo from /api/random, decodes its blurhash
 * into a 64×40 data URL, and writes that URL to `pageTint`.
 *
 * Cheap: one HTTP round-trip + one blurhash decode. The
 * resulting data URL is the same format PhotoGrid pushes, so
 * the backdrop renders identically.
 *
 * Idempotent across navigations: a fresh fetch each mount, so
 * the colour shifts between pages (which the user wants — each
 * page should feel slightly different).
 *
 * Use from `onMount` (or any non-async hook) — fire-and-forget:
 *   onMount(() => { pushRandomTint(); });
 */
export async function pushRandomTint(): Promise<void> {
  try {
    const res = await fetch('/api/random?limit=1');
    if (!res.ok) return;
    const data = await res.json();
    const item = data?.results?.[0];
    const hash = item?.blurhash;
    if (!hash) return;
    const url = await blurhashToDataUrl(hash, 64, 40);
    if (url) pageTint.set(url);
  } catch {
    // Network error, decode failure, etc — safe to ignore. The
    // backdrop falls back to its dark base colour.
  }
}
