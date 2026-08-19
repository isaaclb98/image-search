// lib/feed.js — render the result list as a phone-style single-column feed
//
// Symmetric with lib/grid.js: same `render(rootEl, results, opts)` shape,
// same `append(rootEl, results)`, same sentinel protocol. The data is
// identical (one SearchResult per item, with id/path/score/url); only the
// per-item presentation differs. The frontend (search.js) dispatches based
// on the URL `?view=` param — same API payload, different renderer.
//
// Why a separate file: the layout/structure is materially different from
// the grid (vertical stack, full-width images, no aspect-ratio constraint
// on the cell). Keeping the two renderers in their own files means each
// one stays small and focused, and the diff when toggling view modes is
// local to the JS file the toggle actually affects.

import { enhancePhotoCards, addFavoriteButton } from "./photo-card.js";

export function renderFeed(rootEl, results, opts = {}) {
  if (!rootEl) return;
  // Make the container look like a feed. The class is what the CSS hooks
  // onto; the structural element stays <ul id="result-grid"> so the
  // existing infinite-scroll sentinel and the photo back-link DOM all
  // keep working unchanged.
  rootEl.classList.remove("grid");
  rootEl.classList.add("feed");
  rootEl.innerHTML = "";
  if (results && results.length > 0) {
    appendToFeed(rootEl, results);
  }
  if (opts.hasMore) {
    addSentinel(rootEl);
  } else {
    removeSentinel(rootEl);
  }
  rootEl.dataset.hasMore = opts.hasMore ? "true" : "false";
  if (typeof opts.offset === "number") {
    rootEl.dataset.offset = String(opts.offset + (results?.length || 0));
  }
}

export function appendToFeed(rootEl, results) {
  if (!rootEl || !results || results.length === 0) return;
  const searchParams = currentSearchParams();
  // Remove sentinel before appending, re-add at the end so the
  // IntersectionObserver keeps firing on the last item.
  const sentinel = rootEl.querySelector(":scope > .grid-sentinel");
  if (sentinel) sentinel.remove();

  const frag = document.createDocumentFragment();
  for (const r of results) {
    const li = document.createElement("li");
    li.className = "feed-item";
    li.dataset.id = r.id;
    li.dataset.score = r.score;
    li.dataset.photoId = r.id;
    li.dataset.photoSrc = r.url || "";
    li.dataset.photoPath = r.path || "";

    const a = document.createElement("a");
    a.className = "thumb-link";
    a.href = `/photo/${r.id}${searchParams ? `?${searchParams}` : ""}`;
    a.dataset.lightboxTrigger = "";
    a.dataset.photoId = r.id;
    a.dataset.photoSrc = r.url || "";
    a.dataset.photoPath = r.path || "";
    a.dataset.blurhash = r.blurhash || "";
    a.setAttribute("aria-label", "Open photo");

    const img = document.createElement("img");
    img.src = r.url;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => img.classList.add("img-error"));

    // Same corner badge as the grid, just a different position class.
    // Server pre-formats score_str to 3 decimals so SSR and JS render
    // identically.
    const score = document.createElement("span");
    score.className = "feed-score";
    score.textContent = r.score_str || "";

    a.appendChild(img);
    if (r.score_str) li.appendChild(score);
    li.appendChild(a);
    addFavoriteButton(li, r);
    frag.appendChild(li);
  }
  rootEl.appendChild(frag);

  // Re-add sentinel at the end.
  const sentinelNew = document.createElement("li");
  sentinelNew.className = "grid-sentinel";
  sentinelNew.setAttribute("aria-hidden", "true");
  rootEl.appendChild(sentinelNew);

  enhancePhotoCards(rootEl);
}

export function addSentinel(rootEl) {
  if (!rootEl) return;
  removeSentinel(rootEl);
  const li = document.createElement("li");
  li.className = "grid-sentinel";
  li.setAttribute("aria-hidden", "true");
  rootEl.appendChild(li);
}

export function removeSentinel(rootEl) {
  if (!rootEl) return;
  const existing = rootEl.querySelector(":scope > .grid-sentinel");
  if (existing) existing.remove();
}

function currentSearchParams() {
  const params = new URLSearchParams(window.location.search);
  params.delete("offset");
  params.delete("limit");
  return params.toString();
}
