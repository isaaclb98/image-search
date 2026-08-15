// favorites.js — page logic for /favorites
//
// Infinite scroll: an IntersectionObserver on the sentinel <li> at the
// bottom of the grid fetches the next page from `/api/favorites` and
// appends it. Stops when the API returns `has_more: false`, which
// means we've walked the whole favourites list.
//
// Renderers are shared with search.js / random.js (lib/grid.js and
// lib/feed.js) so the look matches the other pages. The view is
// fixed per page-load — /favorites has no grid/feed toggle yet —
// and the renderer is picked from the class the server already put
// on `#result-grid`.
//
// Why a separate file: the search and random page scripts are heavy
// (centroid anchors, prompt chips, saved-searches, collections,
// filename filter…) and most of that machinery doesn't apply to a
// pure favourites list. Keeping the favourites page logic thin
// mirrors the layout-vs-search split the rest of the app already
// follows.

import { appendToGrid, addSentinel, removeSentinel } from "./lib/grid.js";
import { appendToFeed } from "./lib/feed.js";

const grid = document.getElementById("result-grid");
const loadMoreHint = document.querySelector(".load-more-hint");
const favoritesPage = document.querySelector("[data-favorites-page]");
const favoritesCount = document.querySelector("[data-page-count]");
const favoritesDownload = document.querySelector("[data-favorites-download]");
const favoritesEmpty = document.querySelector("[data-favorites-empty]");
let totalFavorites = Number(favoritesPage?.dataset.favoritesTotal || "0");

let loadingMore = false;
let observer = null;

function updateFavoritesShell() {
  if (favoritesCount) {
    favoritesCount.textContent = `${totalFavorites} ${totalFavorites === 1 ? "photo" : "photos"}`;
  }
  if (favoritesDownload) favoritesDownload.hidden = totalFavorites === 0;
  if (favoritesEmpty) favoritesEmpty.hidden = totalFavorites > 0;
  if (totalFavorites === 0) {
    grid?.remove();
    loadMoreHint?.remove();
    teardownObserver();
  }
}

window.addEventListener("favoritechanged", (event) => {
  if (event.detail?.on === false && totalFavorites > 0) {
    totalFavorites -= 1;
    updateFavoritesShell();
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
  const url = new URL("/api/favorites", window.location.origin);
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
    console.error("favorites loadMore failed", err);
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
// For a favourites list that fits in one page, the API will return
// has_more=false and the observer is never created.
if (grid && grid.querySelector(":scope > .grid-sentinel")) {
  attachObserver();
}
