// lib/grid.js — render the result grid
//
import { enhancePhotoCards, addFavoriteButton } from "./photo-card.js";

// The grid is small (50–200 items per page, up to 500 cumulative
// across pages) so a full rebuild per *page* is fine. We just append
// new items on subsequent pages, never re-render existing ones.

export function renderGrid(rootEl, results, opts = {}) {
  if (!rootEl) return;
  // Strip the feed-mode class if we're toggling back from feed view.
  // renderFeed adds "feed"; renderGrid removes it. Without this the
  // container keeps its feed styling even after the items inside are
  // re-rendered as grid-item <li>s.
  rootEl.classList.remove("feed");
  rootEl.classList.add("grid");
  rootEl.innerHTML = "";
  if (results && results.length > 0) {
    appendToGrid(rootEl, results);
  }
  if (opts.hasMore) {
    addSentinel(rootEl);
  } else {
    removeSentinel(rootEl);
  }
  if (opts.hasMore) {
    rootEl.dataset.hasMore = "true";
  } else {
    rootEl.dataset.hasMore = "false";
  }
  if (typeof opts.offset === "number") {
    rootEl.dataset.offset = String(opts.offset + (results?.length || 0));
  }
}

export function appendToGrid(rootEl, results) {
  if (!rootEl || !results || results.length === 0) return;
  const searchParams = currentSearchParams();
  // The sentinel is the last <li> in the grid (added by renderGrid when
  // hasMore=true). Remove it before appending, then re-add at the end.
  const sentinel = rootEl.querySelector(":scope > .grid-sentinel");
  if (sentinel) sentinel.remove();

  const frag = document.createDocumentFragment();
  for (const r of results) {
    const li = document.createElement("li");
    li.className = "photo-card grid-item";
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

    // Cosine similarity, rendered as a top-right badge. Pre-formatted
    // by the server (3-decimal string) so SSR and JS render identically
    // and we don't risk the client format drifting from the server.
    const score = document.createElement("span");
    score.className = "grid-score";
    score.textContent = r.score_str || "";

    a.appendChild(img);
    if (r.score_str) li.appendChild(score);
    li.appendChild(a);
    addFavoriteButton(li, r);
    frag.appendChild(li);
  }
  rootEl.appendChild(frag);
  
  // Re-add sentinel at the end if the list still has more items.
  if (rootEl.dataset.hasMore === "true") {
      addSentinel(rootEl);
  }
  
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
