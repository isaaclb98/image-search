// dislikes.js — page logic for /dislikes
//
// Infinite scroll: an IntersectionObserver on the sentinel <li> at the
// bottom of the grid fetches the next page from `/api/dislikes` and
// appends it. Stops when the API returns `has_more: false`, which
// means we've walked the whole dislikes list.
//
// Renderers are shared with search.js / random.js (lib/grid.js and
// lib/feed.js) so the look matches the other pages. The view is
// fixed per page-load — /dislikes has no grid/feed toggle yet —
// and the renderer is picked from the class the server already put
// on `#result-grid`.
//
// Why a separate file: the search and random page scripts are heavy
// (centroid anchors, prompt chips, saved-searches, collections,
// filename filter…) and most of that machinery doesn't apply to a
// pure dislikes list. Keeping the dislikes page logic thin
// mirrors the layout-vs-search split the rest of the app already
// follows.

import { appendToGrid, addSentinel, removeSentinel } from "./lib/grid.js";
import { appendToFeed } from "./lib/feed.js";

const grid = document.getElementById("result-grid");
const loadMoreHint = document.querySelector(".load-more-hint");
const dislikesPage = document.querySelector("[data-dislikes-page]");
const dislikesCount = document.querySelector("[data-page-count]");
const dislikesDownload = document.querySelector("[data-dislikes-download]");
const dislikesEmpty = document.querySelector("[data-dislikes-empty]");
let totalDislikes = Number(dislikesPage?.dataset.dislikesTotal || "0");

let loadingMore = false;
let observer = null;

function updateDislikesShell() {
  if (dislikesCount) {
    dislikesCount.textContent = `${totalDislikes} ${totalDislikes === 1 ? "photo" : "photos"}`;
  }
  if (dislikesDownload) dislikesDownload.hidden = totalDislikes === 0;
  if (dislikesEmpty) dislikesEmpty.hidden = totalDislikes > 0;
  if (totalDislikes === 0) {
    grid?.remove();
    loadMoreHint?.remove();
    teardownObserver();
  }
}

window.addEventListener("dislikechanged", (event) => {
  if (event.detail?.on === false && totalDislikes > 0) {
    totalDislikes -= 1;
    updateDislikesShell();
  }
});

// Sentinel observer lifecycle. Each loadMorePage tears down and
// recreates the observer after appending, because the old sentinel
// <li> is replaced with a fresh one for the next page. Same shape
// as random.js — separate observers per cycle is intentional.

function getAppender() {
  if (grid && grid.classList.contains("feed")) return appendToFeed;
  return appendToGrid;
}

function buildApiUrl() {
  const url = new URL("/api/dislikes", window.location.origin);
  url.searchParams.set("as_results", "true");
  const offset = Number(grid?.dataset.offset || "0");
  const limit = Number(grid?.dataset.limit || "35");
  url.searchParams.set("offset", String(offset));
  url.searchParams.set("limit", String(limit));
  return url.toString();
}

async function loadMorePage() {
  if (loadingMore || !grid) return;
  if (grid.dataset.hasMore !== "true") return;
  loadingMore = true;
  try {
    const resp = await fetch(buildApiUrl(), {
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) throw new Error(`API ${resp.status}`);
    const data = await resp.json();
    if (!data.results || data.results.length === 0) {
      // End of list. Tear down observer; leave the hint as-is — the
      // SSR template already renders the "Showing the first N" line
      // when the whole list fit in one page.
      grid.dataset.hasMore = "false";
      removeSentinel(grid);
      teardownObserver();
      updateLoadMoreHint(false, data.offset, 0);
      return;
    }
    getAppender()(grid, data.results);
    grid.dataset.offset = String(data.offset + data.results.length);
    grid.dataset.hasMore = data.has_more ? "true" : "false";
    if (data.has_more) {
      addSentinel(grid);
      attachObserver();
    } else {
      teardownObserver();
    }
    updateLoadMoreHint(data.has_more, data.offset, data.results.length);
  } catch (err) {
    console.error("dislikes loadMore failed", err);
    // Leave the sentinel in place so the next scroll retries.
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

function updateLoadMoreHint(hasMore, offset, count) {
  if (!loadMoreHint) return;
  if (hasMore) {
    loadMoreHint.textContent = "Scroll for more results.";
    loadMoreHint.classList.remove("load-more-hint--capped");
  } else if (count > 0) {
    const total = Number(loadMoreHint.dataset.maxTotal || "500");
    if (offset + count >= total) {
      loadMoreHint.textContent = `Showing the first ${total} results.`;
      loadMoreHint.classList.add("load-more-hint--capped");
    } else {
      loadMoreHint.textContent = "";
    }
  } else {
    loadMoreHint.textContent = "";
  }
}

// Initial attachment: only wire the observer when the SSR HTML
// actually has a sentinel (i.e. there are more pages to fetch).
// For a dislikes list that fits in one page, the API will return
// has_more=false and the observer is never created.
if (grid && grid.querySelector(":scope > .grid-sentinel")) {
  attachObserver();
}
