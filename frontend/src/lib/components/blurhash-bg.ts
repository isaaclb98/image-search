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
