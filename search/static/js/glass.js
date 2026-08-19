// lib/glass.js
// Wires photo-derived colour tokens into CSS custom properties.
//
// Two consumers, one pipeline:
//   - Per card (.photo-card): on IntersectionObserver entry, decode
//     the photo's blurhash and set --photo-dominant / --photo-avg /
//     --photo-palette-* on the card element.
//   - Per page (body): set --page-accent and the body's
//     --photo-palette-* to the top-most visible result's palette, so
//     the ambient mesh in layout.css adopts the current "vibe".
//
// Computes once per blurhash and memoise via extractColors. We hand
// the decoding to colors.js which reads from a single pixel buffer
// reused across calls.

import { extractColors, ambientStops } from "./lib/colors.js";

const BATCH_SIZE = 6;        // cards per microtask tick
const OBSERVER_ROOT_MARGIN = "200px 0px";  // start work before scroll-in

let cardsObserved = false;
let pageAccentSet = false;

function applyCardColors(card, colors) {
  if (!colors) return;
  card.style.setProperty("--photo-dominant", colors.dominant.str);
  card.style.setProperty("--photo-avg",      colors.avg.str);
  if (colors.palette[0]) card.style.setProperty("--photo-palette-1", colors.palette[0]);
  if (colors.palette[1]) card.style.setProperty("--photo-palette-2", colors.palette[1]);
  if (colors.palette[2]) card.style.setProperty("--photo-palette-3", colors.palette[2]);
  card.classList.add("is-colored");
}

function cardBlurhash(card) {
  // The result-grid macro puts data-blurhash on the inner <a>; the
  // photo-detail macro puts it directly on the .photo-figure. We
  // honour both.
  return (
    card.dataset.photoBlurhash ||
    card.dataset.blurhash ||
    card.querySelector("[data-blurhash]")?.dataset.blurhash ||
    ""
  );
}

function processCard(card) {
  if (card.classList.contains("is-colored")) return Promise.resolve();
  const bh = cardBlurhash(card);
  if (!bh || bh === "None") return Promise.resolve();
  return new Promise((resolve) => {
    // Yield to event loop so the painting doesn't block scrolling.
    requestAnimationFrame(() => {
      const colors = extractColors(bh);
      applyCardColors(card, colors);
      resolve(colors);
    });
  });
}

async function processInBatches(cards) {
  for (let i = 0; i < cards.length; i += BATCH_SIZE) {
    const batch = cards.slice(i, i + BATCH_SIZE);
    await Promise.all(batch.map(processCard));
    // If the page accent hasn't been set yet (no [data-page-accent-from]
    // on this page), derive it from the first card that has a blurhash.
    // This makes the ambient mesh and glass tint work on dynamically
    // loaded grids like /for-you and /search results.
    if (!pageAccentSet) {
      for (const card of batch) {
        const bh = cardBlurhash(card);
        if (bh) {
          setPageAccent(bh);
          pageAccentSet = true;
          break;
        }
      }
    }
    // Give the rendering thread a tick between batches.
    await new Promise((r) => requestAnimationFrame(r));
  }
}

function setupCardObserver() {
  if (cardsObserved) return;
  if (!("IntersectionObserver" in window)) {
    // Fallback: just process every card on page load.
    processInBatches([...document.querySelectorAll(".photo-card")]);
    return;
  }

  const queue = [];
  const obs = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && !entry.target.classList.contains("is-colored")) {
          queue.push(entry.target);
        }
      }
      if (queue.length) {
        const batch = queue.splice(0, BATCH_SIZE);
        processInBatches(batch).then(() => {
          if (queue.length) {
            // Chain any backlog on the next tick.
            requestAnimationFrame(() => {
              processInBatches(queue.splice(0, queue.length));
            });
          }
        });
      }
    },
    { rootMargin: OBSERVER_ROOT_MARGIN, threshold: 0.01 },
  );

  for (const card of document.querySelectorAll(".photo-card")) {
    obs.observe(card);
  }
  cardsObserved = true;
}

/**
 * Set the page-level ambient colour (used by layout.css mesh, search-bar
 * glow). Call once with the "headlining" photo's blurhash.
 */
function setPageAccent(blurhash) {
  if (!blurhash) return;
  const stops = ambientStops(blurhash);
  if (!stops) return;
  const body = document.body;
  body.style.setProperty("--photo-palette-1", stops[0]);
  body.style.setProperty("--photo-palette-2", stops[1]);
  body.style.setProperty("--photo-palette-3", stops[2]);
  // Most-saturated stop becomes the primary accent.
  const c = extractColors(blurhash);
  if (c) {
    body.style.setProperty("--page-accent", c.accent);
    // Derive a glass tint: same hue, very low saturation, high lightness.
    // This makes frosted panels pick up the image's colour family without
    // competing with the content.  Format: "H S% L%" — same as other
    // HSL custom properties.
    const parts = c.accent.split(/\s+/);
    if (parts.length >= 3) {
      const h = parts[0];
      body.style.setProperty("--glass-tint", h + " 5% 93%");
      body.style.setProperty("--glass-tint-fg", h + " 40% 40%");
    }
  }
}

/**
 * Apply the blurred-photo backdrop to the photo detail page.
 * Uses the .photo-page-bg element if present; sets its background-image.
 */
function setupPhotoBackdrop() {
  const bg = document.querySelector(".photo-page-bg[data-photo-src]");
  if (!bg) return;
  // Lazy-apply so LCP image still gets preload priority.
  const apply = () => {
    bg.style.backgroundImage = `url("${bg.dataset.photoSrc}")`;
    bg.classList.add("has-photo");
  };
  if ("requestIdleCallback" in window) {
    requestIdleCallback(apply, { timeout: 600 });
  } else {
    setTimeout(apply, 250);
  }
}

/**
 * Public init. Call from a <script type="module"> at end of body.
 * Safe to call multiple times — tracks "already observed" state.
 */
export function initGlass() {
  setupCardObserver();
  setupPhotoBackdrop();

  // Page-level accent: prefer [data-page-accent-from] (e.g. body),
  // else the first visible result's blurhash.
  const trigger =
    document.querySelector("[data-page-accent-from]") ||
    document.querySelector(".photo-card[data-photo-blurhash]");
  if (trigger) {
    const bh = trigger.dataset.pageAccentFrom || cardBlurhash(trigger);
    if (bh) {
      setPageAccent(bh);
      pageAccentSet = true;
    }
  }
}

// Auto-init on DOMContentLoaded so static pages work without
// explicit script wiring. Pages can also call initGlass() earlier
// during their own module bootstrap.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initGlass, { once: true });
} else {
  initGlass();
}
