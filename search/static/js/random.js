// random.js — page logic for /random
//
// Interaction model:
//   - View toggle (grid/feed): re-render the *current* sample with a
//     different renderer. No network call — the response is held in JS
//     state so toggling is instant.
//   - Infinite scroll: an IntersectionObserver on the sentinel <li>
//     fires when the user reaches the bottom. Each scroll fetches a
//     fresh random sample from /api/random and appends it. The
//     sentinel stays until an API call returns fewer than `limit`
//     rows, signalling "collection exhausted, stop scrolling".
//
// Renderers and view-toggle UX are shared with search.js via lib/grid.js,
// lib/feed.js, and the .view-toggle-btn CSS. The page is intentionally
// a thin wrapper around those primitives so the look matches the search
// page exactly.

import { renderGrid, appendToGrid, addSentinel, removeSentinel } from "./lib/grid.js";
import { renderFeed, appendToFeed } from "./lib/feed.js";

const grid = document.getElementById("result-grid");

// In-memory state. We keep every result rendered so far so the view
// toggle can re-render the whole sample without a network round-trip,
// and so /api/random could later be told which ids to exclude.
let currentResults = [];

// Sentinel observer lifecycle. Each loadMorePage tears down and
// recreates the observer after appending, because the old sentinel
// <li> is replaced with a fresh one for the next page.
let loadingMore = false;
let observer = null;

/**
 * Pick a renderer pair for the given view. Mirrors the dispatcher in
 * search.js but without masonry (out of scope for this branch).
 */
function getRenderer(view) {
  if (view === "feed") return { render: renderFeed, append: appendToFeed };
  return { render: renderGrid, append: appendToGrid };
}

function readView() {
  const params = new URLSearchParams(window.location.search);
  const v = params.get("view");
  if (v === "feed") return "feed";
  return "grid";
}

function readCollections() {
  const params = new URLSearchParams(window.location.search);
  return params.getAll("collections");
}

function readLimit() {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("limit");
  const n = parseInt(raw || "", 10);
  return Number.isFinite(n) && n > 0 ? n : 70;
}

function syncViewToggle() {
  const current = readView();
  for (const btn of document.querySelectorAll(".view-toggle-btn")) {
    const isActive = btn.dataset.view === current;
    btn.classList.toggle("view-toggle-btn--active", isActive);
    btn.setAttribute("aria-pressed", isActive ? "true" : "false");
  }
}

function renderCurrent() {
  if (!grid) return;
  const renderer = getRenderer(readView());
  renderer.render(grid, currentResults, { hasMore: grid.dataset.hasMore === "true" });
}

function buildApiUrl() {
  const url = new URL("/api/random", window.location.origin);
  for (const c of readCollections()) {
    url.searchParams.append("collections", c);
  }
  url.searchParams.set("limit", String(readLimit()));
  url.searchParams.set("view", readView());
  return url.toString();
}

async function loadMorePage() {
  if (loadingMore || !grid) return;
  if (grid.dataset.hasMore !== "true") return;
  loadingMore = true;
  try {
    const resp = await fetch(buildApiUrl(), { headers: { Accept: "application/json" } });
    if (!resp.ok) throw new Error(`API ${resp.status}`);
    const data = await resp.json();
    if (!data.results || data.results.length === 0) {
      // Collection exhausted. Sentinel stays away; observer disconnects.
      grid.dataset.hasMore = "false";
      removeSentinel(grid);
      teardownObserver();
      return;
    }
    const renderer = getRenderer(readView());
    renderer.append(grid, data.results);
    currentResults = currentResults.concat(data.results);
    if (data.has_more) {
      addSentinel(grid);
      attachObserver();
    } else {
      grid.dataset.hasMore = "false";
      removeSentinel(grid);
      teardownObserver();
    }
  } catch (e) {
    console.error("random loadMore failed", e);
    // Leave the sentinel in place so a retry is possible on the next
    // scroll. Lock is released in the finally block below.
  } finally {
    loadingMore = false;
  }
}

function attachObserver() {
  if (!grid) return;
  teardownObserver();
  const sentinel = grid.querySelector(":scope > .grid-sentinel");
  if (!sentinel) return;
  if (!("IntersectionObserver" in window)) return;
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          loadMorePage();
          break;
        }
      }
    },
    { rootMargin: "400px 0px 400px 0px" }
  );
  observer.observe(sentinel);
}

function teardownObserver() {
  if (observer) {
    observer.disconnect();
    observer = null;
  }
}

function onViewToggleClick(nextView) {
  if (readView() === nextView) return;
  const url = new URL(window.location.href);
  if (nextView === "grid") {
    url.searchParams.delete("view");
  } else {
    url.searchParams.set("view", nextView);
  }
  history.pushState({}, "", url.pathname + (url.search || ""));
  syncViewToggle();
  // Re-render current results with the new view — no fetch needed.
  renderCurrent();
}

// Boot — hydrate JS state from the SSR data block so the view toggle
// can re-render the server-rendered sample without an extra API call.
const ssrData = document.getElementById("random-initial-results");
if (ssrData && ssrData.textContent) {
  try {
    currentResults = JSON.parse(ssrData.textContent);
  } catch (e) {
    console.warn("failed to parse SSR results", e);
  }
}

syncViewToggle();
for (const btn of document.querySelectorAll(".view-toggle-btn")) {
  btn.addEventListener("click", () => onViewToggleClick(btn.dataset.view));
}
window.addEventListener("popstate", () => {
  syncViewToggle();
  renderCurrent();
});

// Initial infinite scroll: only attach if the SSR HTML has a sentinel
// (i.e. there are results and we haven't hit the end of the collection
// on first paint). For a tiny collection that fits in one batch, the
// API will return has_more=false and the observer is never wired.
if (grid && grid.querySelector(":scope > .grid-sentinel")) {
  attachObserver();
}