/**
 * Tiny blurhash decoder for LQIP tinting.
 *
 * Decode a blurhash to a small data URL we can drop in as a
 * background behind a glass surface. Keeps the bundle small —
 * the heavy work happens on demand.
 *
 * If anything fails (no blurhash, malformed, browser doesn't
 * support canvas), returns null so the caller falls through.
 *
 * Round-4 perf: pooled canvas + LRU hash cache.
 *
 * Before this round, every <PhotoTile> mount called
 * blurhashToDataUrl() which:
 *   - created a fresh <canvas> + 2d context
 *   - drew the decoded pixels
 *   - ran canvas.toDataURL() (PNG encode)
 *   - discarded the canvas
 *
 * On a 28-tile first paint that's 28 canvas allocs + 28 PNG
 * encodes — all synchronous on the main thread. Tiles
 * mounting/unmounting via the virtualizer scaled this to
 * hundreds per scroll cycle on /random.
 *
 * Optimisations:
 *   1. LRU cache keyed on `${hash}|${w}|${h}` — many blurhash
 *      values recur across the app (same photo appearing in
 *      multiple lists, album cover vs. photo-page visit, etc.).
 *      Cache hits skip both the decode and the canvas round-
 *      trip. 256-entry cap balances memory (~16 KB of keys +
 *      ~1 MB of data URLs at 64x40) against hit rate.
 *   2. Single shared <canvas> reused across all calls.
 *      createElement + getContext are cheap but not free, and
 *      on iOS Safari the per-canvas memory accounting is the
 *      slowest part. Reusing the same canvas keeps it warm
 *      in the engine.
 *
 * The public API is unchanged — callers don't need to know
 * about the cache.
 */

let _mod: Promise<typeof import('blurhash')> | null = null;
async function load() {
  if (!_mod) _mod = import('blurhash');
  return _mod;
}

// Module-level shared canvas. Lazy-init so SSR / non-browser
// callers (tests in jsdom) don't trip on document access.
let _canvas: HTMLCanvasElement | null = null;
let _ctx: CanvasRenderingContext2D | null = null;
function getCanvas(w: number, h: number): CanvasRenderingContext2D | null {
  if (typeof document === 'undefined') return null;
  if (!_canvas) {
    _canvas = document.createElement('canvas');
  }
  // Setting width/height resets the canvas state and resizes
  // the backing store — but it's a no-op when the size is
  // unchanged, so calls with the same dimensions don't churn.
  _canvas.width = w;
  _canvas.height = h;
  if (!_ctx) {
    _ctx = _canvas.getContext('2d');
  }
  return _ctx;
}

// LRU cache. Map preserves insertion order; when we hit a
// cached key, we delete + re-set it to move it to the tail
// (most-recently-used). On overflow, we evict the head (oldest).
const CACHE_LIMIT = 256;
const _cache = new Map<string, string>();

function cacheGet(key: string): string | undefined {
  const v = _cache.get(key);
  if (v !== undefined) {
    _cache.delete(key);
    _cache.set(key, v);
  }
  return v;
}

function cacheSet(key: string, value: string): void {
  if (_cache.has(key)) {
    _cache.delete(key);
  } else if (_cache.size >= CACHE_LIMIT) {
    // Evict oldest (head of insertion-ordered Map).
    const oldest = _cache.keys().next().value;
    if (oldest !== undefined) _cache.delete(oldest);
  }
  _cache.set(key, value);
}

/** Test-only: clear the canvas pool + LRU cache. */
export function _resetBlurhashCache(): void {
  _cache.clear();
  _canvas = null;
  _ctx = null;
}

export async function blurhashToDataUrl(
  hash: string | null | undefined,
  w = 32,
  h = 32
): Promise<string | null> {
  if (!hash) return null;
  const key = `${hash}|${w}|${h}`;
  const cached = cacheGet(key);
  if (cached !== undefined) return cached;
  try {
    const bh = await load();
    const pixels = bh.decode(hash, w, h, 1);
    const ctx = getCanvas(w, h);
    if (!ctx) return null;
    const img = ctx.createImageData(w, h);
    img.data.set(pixels);
    ctx.putImageData(img, 0, 0);
    const url = _canvas!.toDataURL();
    cacheSet(key, url);
    return url;
  } catch {
    return null;
  }
}
