// lib/colors.js
// Extract photo-derived color tokens from a BlurHash.
//
// The indexer stores one BlurHash per photo in the Qdrant payload.
// Decoding those 30×30 pixels gives us a tiny but representative
// sample of the photo's colors — enough to drive glass tinting
// without ever fetching the real image bytes.
//
// Outputs (HSL "triple" form, e.g. "217 65% 47%" so callers can
// splice into hsl(...) directly):
//   avg           — average color (linear average of pixels)
//   dominant      — mode-bucket color (most pixels land here)
//   palette       — three dominant stops (for ambient mesh bg)
//   accent        — biased away from pure-near-grayscale hues; the
//                   "colour" the photo is actually about
//
// Result is memoized by BlurHash string in localStorage for 7 days
// so revisiting a grid doesn't repeat the work.

import { decodeBlurHash } from "./blurhash.js";

const MEMO_PREFIX = "img-color:";
const MEMO_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days
const DECODE_W = 32;
const DECODE_H = 32;
const PALETTE_BUCKETS = 12; // 12 hue buckets × 3 lightness = 36 buckets

// Pixel buffer reused across calls (decodeBlurHash writes into it).
let pixelBuf = null;

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r)      h = ((g - b) / d + (g < b ? 6 : 0));
    else if (max === g) h = (b - r) / d + 2;
    else                h = (r - g) / d + 4;
    h *= 60;
  }
  return [h, s * 100, l * 100];
}

function hslTriple(rgb) {
  const [h, s, l] = rgbToHsl(rgb[0], rgb[1], rgb[2]);
  return {
    h: Math.round(h),
    s: Math.round(s),
    l: Math.round(l),
    str: `${Math.round(h)} ${Math.round(s)}% ${Math.round(l)}%`,
  };
}

// Bucket function — coarse quantize HSL so we can find dominant bins.
function bucket(hsl) {
  // Hue is circular — map to a 0..PALETTE_BUCKETS-1 bin of 30° each.
  const hBin = Math.floor(hsl[0] / 30) % PALETTE_BUCKETS;
  // Lightness into 3 bins (dark/mid/light) so a white sky doesn't
  // dominate everything.
  const lBin = Math.min(2, Math.floor(hsl[2] / 34));
  // Skip near-achromatic — those contribute little to "colour"
  // and inflate the dominant count.
  return { key: `${hBin}-${lBin}`, hBin, lBin, s: hsl[1], l: hsl[2] };
}

function decodePixels(blurhash) {
  if (!pixelBuf || pixelBuf.length !== DECODE_W * DECODE_H * 4) {
    pixelBuf = new Uint8ClampedArray(DECODE_W * DECODE_H * 4);
  }
  // decodeBlurHash returns a fresh Uint8ClampedArray — copy into reusable buf.
  const out = decodeBlurHash(blurhash, DECODE_W, DECODE_H, 1);
  pixelBuf.set(out);
  return pixelBuf;
}

function pixelsToColors(pixels) {
  const total = pixels.length / 4;
  let rAvg = 0, gAvg = 0, bAvg = 0;

  // Bucket accumulator: key → { count, rSum, gSum, bSum }
  const buckets = new Map();

  for (let i = 0; i < pixels.length; i += 4) {
    const r = pixels[i];
    const g = pixels[i + 1];
    const b = pixels[i + 2];
    const a = pixels[i + 3];
    if (a === 0) continue;

    rAvg += r; gAvg += g; bAvg += b;

    const hsl = rgbToHsl(r, g, b);
    // Skip achromatic (very low saturation) so the dominant bucket
    // reflects actual colour, not white/black balance.
    if (hsl[1] < 8) continue;
    const b_ = bucket(hsl);
    const acc = buckets.get(b_.key) || { count: 0, rSum: 0, gSum: 0, bSum: 0 };
    acc.count += 1;
    acc.rSum += r;
    acc.gSum += g;
    acc.bSum += b;
    buckets.set(b_.key, acc);
  }

  if (total === 0) {
    // Defensive fallback — solid neutral gray-blue.
    return {
      avg:      { rgb: [128, 130, 140], hsl: { str: "220 8% 50%" }, str: "220 8% 50%" },
      dominant: { rgb: [128, 130, 140], hsl: { str: "220 8% 50%" }, str: "220 8% 50%" },
      palette:  ["220 8% 50%", "220 8% 50%", "220 8% 50%"],
      accent:   "220 35% 45%",
    };
  }

  rAvg = Math.round(rAvg / total);
  gAvg = Math.round(gAvg / total);
  bAvg = Math.round(bAvg / total);

  // Sort buckets by count desc. Top N go to palette.
  const sorted = [...buckets.values()].sort((a, b) => b.count - a.count);

  // Take up to 3 distinct buckets, skipping neighbours by >30° hue if possible.
  const paletteRgb = [];
  for (const acc of sorted) {
    if (paletteRgb.length === 3) break;
    const rgb = [
      Math.round(acc.rSum / acc.count),
      Math.round(acc.gSum / acc.count),
      Math.round(acc.bSum / acc.count),
    ];
    // Diff from existing by saturation-weighted hue distance
    const lastHsl = paletteRgb.length ? rgbToHsl(...paletteRgb[paletteRgb.length - 1]) : null;
    const thisHsl = rgbToHsl(...rgb);
    if (lastHsl && Math.abs(lastHsl[0] - thisHsl[0]) < 25 && thisHsl[1] > 20) continue;
    paletteRgb.push(rgb);
  }

  // Pad palette to length 3 if collapse left us short.
  while (paletteRgb.length < 3) {
    paletteRgb.push([rAvg, gAvg, bAvg]);
  }

  const dominantRgb = sorted.length
    ? [
        Math.round(sorted[0].rSum / sorted[0].count),
        Math.round(sorted[0].gSum / sorted[0].count),
        Math.round(sorted[0].bSum / sorted[0].count),
      ]
    : [rAvg, gAvg, bAvg];

  // Accent = first palette colour with non-trivial saturation,
  // otherwise the dominant one. This is what UI pins to.
  const accentRgb = paletteRgb.find(([r, g, b]) => rgbToHsl(r, g, b)[1] > 18) || dominantRgb;
  const accentHsl = rgbToHsl(...accentRgb);

  return {
    avg:      { rgb: [rAvg, gAvg, bAvg], str: `${rgbToHsl(rAvg, gAvg, bAvg)[0].toFixed(0)} ${rgbToHsl(rAvg, gAvg, bAvg)[1].toFixed(0)}% ${rgbToHsl(rAvg, gAvg, bAvg)[2].toFixed(0)}%` },
    dominant: { rgb: dominantRgb, str: `${rgbToHsl(...dominantRgb)[0].toFixed(0)} ${rgbToHsl(...dominantRgb)[1].toFixed(0)}% ${rgbToHsl(...dominantRgb)[2].toFixed(0)}%` },
    palette:  paletteRgb.map(([r, g, b]) => {
      const h = rgbToHsl(r, g, b);
      return `${h[0].toFixed(0)} ${h[1].toFixed(0)}% ${h[2].toFixed(0)}%`;
    }),
    accent:   `${accentHsl[0].toFixed(0)} ${accentHsl[1].toFixed(0)}% ${accentHsl[2].toFixed(0)}%`,
  };
}

function memoKey(blurhash) {
  return MEMO_PREFIX + blurhash.slice(0, 24);
}

function readMemo(blurhash) {
  try {
    const raw = localStorage.getItem(memoKey(blurhash));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (Date.now() - parsed.t > MEMO_TTL_MS) return null;
    return parsed.v;
  } catch (_) {
    return null;
  }
}

function writeMemo(blurhash, value) {
  try {
    localStorage.setItem(memoKey(blurhash), JSON.stringify({ t: Date.now(), v: value }));
  } catch (_) {
    // localStorage full or unavailable — skip silently.
  }
}

/**
 * Extract photo-derived color tokens from a BlurHash string.
 * Returns null on bad input.
 *
 * @param {string} blurhash  e.g. "L6PZfSi_.AyE_3t7t7R**0o#DgR4"
 * @returns {{avg: {rgb, str}, dominant: {rgb, str},
 *           palette: string[3], accent: string}|null}
 */
export function extractColors(blurhash) {
  if (typeof blurhash !== "string" || blurhash.length < 6) return null;
  const memo = readMemo(blurhash);
  if (memo) return memo;

  let pixels;
  try {
    pixels = decodePixels(blurhash);
  } catch (_) {
    return null;
  }

  const result = pixelsToColors(pixels);
  writeMemo(blurhash, result);
  return result;
}

/**
 * Compute a balanced tint for the page-level ambient mesh: pick the
 * three palette stops with the highest "punch" (mid-tone, sat > 20%)
 * and fill remaining slots with the dominant.
 */
export function ambientStops(blurhash) {
  const c = extractColors(blurhash);
  if (!c) return null;
  return c.palette; // length 3
}
