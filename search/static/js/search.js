// search.js — page-level controller
//
// Behavior contract:
//   - Form submission is intercepted when a results view is on screen
//     (i.e. grid + result-count exist); we push the new URL onto the
//     history stack and re-run the search via /api/search.
//   - On the landing page (no results view yet), the form does a real
//     full-page submit. SSR renders the grid + result-count, after
//     which subsequent submissions can be intercepted.
//   - popstate re-runs the search so back/forward always shows the
//     right results for the URL.
//   - Infinite scroll: an IntersectionObserver on a sentinel <li> at
//     the bottom of the grid fetches the next page via /api/search
//     and appends results, until has_more=false or 500-result cap.
//   - Library chip filter: a row of toggle chips above the results
//     is populated from /api/collections. Each chip is a multi-select
//     filter on the `?collection=` query param.

import { buildSearchUrlWithFilename, readCentroid, readCentroidWeights, readCentroids, readCollections, readDiverse, readFavoritesFilter, readFilename, readPrompts, readQuery, readView } from "./lib/url.js"
import { renderGrid, appendToGrid, addSentinel, removeSentinel } from "./lib/grid.js";
import { renderFeed, appendToFeed } from "./lib/feed.js";
import { PromptChips } from "./lib/prompts.js";

const form = document.querySelector(".search-form");
const input = form?.querySelector('input[name="q"]');
const filenameInput = form?.querySelector('input[name="filename"]');
const resultCount = document.querySelector(".result-count");
const grid = document.getElementById("result-grid");
const loadMoreHint = document.querySelector(".load-more-hint");
const categoryBar = document.getElementById("category-bar");
const promptRoot = document.querySelector(".prompt-composition");
const promptError = document.querySelector(".prompt-error");
const filterSummary = document.querySelector("[data-filter-summary]");
const promptChips = promptRoot ? new PromptChips(promptRoot, readPrompts()) : null;

let loadingMore = false;
let io = null;

// Populate the library chip filter from /api/collections. Called on
// page load. Each chip, when clicked, toggles its library in the URL
// and re-runs the search. Active state is derived from `?collection=`
// params in the URL.
populateCollectionChips();

if (form && input) {
  form.addEventListener("submit", (e) => {
    const q = input.value.trim();
    const filename = filenameInput ? filenameInput.value.trim() : "";
    e.preventDefault();
    if (!hasPositivePrompt(q, filename)) {
      showPromptError("Add at least one include prompt or filename.");
      return;
    }
    clearPromptError();
    // buildSearchUrlWithFilename takes the filename from the form's
    // current value (the user just typed it), not from the URL —
    // the URL hasn't been updated yet. Empty filename is omitted
    // from the canonical URL so the "clear" link stays clean.
    const url = buildSearchUrlWithFilename(
      q, promptChips?.serialize(), activeCollections(), filename,
    );
    history.pushState({ q }, "", url);
    if (!grid || !resultCount) {
      window.location.href = url;
      return;
    }
    runSearch();
  });
}

window.addEventListener("popstate", () => {
  const q = readQuery();
  if (input) input.value = q;
  if (filenameInput) filenameInput.value = readFilename();
  promptChips?.hydrate(readPrompts());
  promptChips?.render();
  syncViewToggle();
  runSearch();
});

// Infinite scroll setup. Only attaches when the initial page rendered
// with has_more=true (i.e. a sentinel <li> is in the DOM).
attachInfiniteScroll();

async function runSearch() {
  const q = input ? input.value.trim() : readQuery().trim();
  if (!hasActiveSearch(q)) {
    // Empty query: full reload to the bare / page. This keeps the
    // server-rendered "Enter a query" message visible. hasActiveSearch
    // also returns true for centroid-only pages, so we don't redirect
    // a centroid URL away just because the text input is empty.
    window.location.href = "/";
    return;
  }

  setLoading(true);

  try {
    const apiUrl = buildApiUrl(q);
    const resp = await fetch(apiUrl, {
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) {
      showError();
      return;
    }
    const data = await resp.json();
    renderInitial(data);
    // Re-render chips so active state matches the (now possibly
    // changed) URL — important when the user used a chip to narrow
    // a search.
    syncChipActiveState();
  } catch (err) {
    console.error("search failed", err);
    showError();
  } finally {
    setLoading(false);
  }
}

function buildApiUrl(q, surprise = false) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  // Forward every active centroid anchor. The server treats any
  // non-empty `?centroid=` list as a valid search anchor on its
  // own (mutex with text prompts) and will 400 on empty requests.
  // The form input is disabled in centroid mode, but popstate /
  // view toggle / chip clicks all re-enter this path, so we have
  // to forward every name. We forward `?weights=` too when not
  // all-equal so a non-default blend round-trips correctly.
  const centroids = readCentroids();
  for (const c of centroids) params.append("centroid", c);
  const weights = readCentroidWeights();
  if (weights && centroids.length > 1 && weights.length === centroids.length) {
    params.set("weights", weights.join(","));
  }
  if (promptChips) {
    for (const [key, value] of promptChips.serialize().entries()) {
      params.append(key, value);
    }
  }
  // Multi-value: every active chip adds a `collection` param. The
  // server applies them as a MatchAny filter.
  for (const c of activeCollections()) {
    params.append("collection", c);
  }
  // Filename: round-trip from the URL so the API request honours
  // the same filter as the page that built it. Empty values are
  // omitted so the server treats it as "no filter".
  const filename = readFilename();
  if (filename) {
    params.set("filename", filename);
  }
  if (readFavoritesFilter()) {
    params.set("favorites", "true");
  }
  if (readDiverse()) {
    params.set("diverse", "true");
  }
  if (surprise) {
    params.set("surprise", "true");
  }
  // Echo the view back so the response JSON includes it; the server
  // is the source of truth and would coerce invalid values anyway.
  const view = readView();
  if (view && view !== "grid") {
    params.set("view", view);
  }
  return `/api/search?${params.toString()}`;
}

function activeCollections() {
  return readCollections();
}

function renderInitial(data) {
  if (resultCount) {
    const n = data.results.length;
    const start = n > 0 ? 1 : 0;
    const end = data.offset + n;
    // Centroid wins over text — the SSR template branches the same
    // way. Without this, popstate / view toggle / chip click in
    // centroid mode would re-render the count as `results for ""`.
    const label = data.centroid
      ? `centroid "${data.centroid}"`
      : (data.query || (data.positives || []).join(", "));
    if (n > 0) {
      const moreText = data.has_more
        ? `(of more than ${end})`
        : "";
      resultCount.textContent =
        `Showing ${start}\u2013${end} ${moreText} results for "${label}" ` +
        `(${data.took_ms} ms)`;
    } else {
      resultCount.textContent = `No results for "${label}".`;
    }
  }
  if (grid) {
    const renderer = getRenderer(readView());
    renderer.render(grid, data.results, {
      hasMore: data.has_more,
      offset: data.offset,
    });
  }
  teardownInfiniteScroll();
  attachInfiniteScroll();
  updateLoadMoreHint(data.has_more, data.offset, data.results.length);
}

async function loadMorePage() {
  if (loadingMore || !grid) return;
  const q = readQuery().trim();
  const filename = readFilename();
  if (!hasPositivePrompt(q, filename)) return;
  const currentOffset = Number(grid.dataset.offset || "0");
  const limit = Number(grid.dataset.limit || "50");
  const nextOffset = currentOffset;

  loadingMore = true;
  setLoading(true);
  try {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    // Same forwarding rule as buildApiUrl — without this, the API
    // would 400 ("at least one positive prompt is required") and the
    // sentinel would just sit there as the user scrolls.
    const centroid = readCentroid();
    if (centroid) params.set("centroid", centroid);
    if (filename) params.set("filename", filename);
    params.set("offset", String(nextOffset));
    params.set("limit", String(limit));
    if (promptChips) {
      for (const [key, value] of promptChips.serialize().entries()) {
        params.append(key, value);
      }
    }
    for (const c of activeCollections()) {
      params.append("collection", c);
    }
    if (readFavoritesFilter()) {
      params.set("favorites", "true");
    }
    if (readDiverse()) {
      params.set("diverse", "true");
    }
    const view = readView();
    if (view && view !== "grid") {
      params.set("view", view);
    }
    const resp = await fetch(`/api/search?${params.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) {
      showError();
      return;
    }
    const data = await resp.json();
    if (data.results.length === 0) {
      // No more results. Tear down the observer.
      grid.dataset.hasMore = "false";
      removeSentinel(grid);
      teardownInfiniteScroll();
      updateLoadMoreHint(false, data.offset, 0);
      return;
    }
    const renderer = getRenderer(readView());
    renderer.append(grid, data.results);
    grid.dataset.offset = String(data.offset + data.results.length);
    grid.dataset.hasMore = data.has_more ? "true" : "false";
    if (data.has_more) {
      addSentinel(grid);
      attachInfiniteScroll();  // new sentinel needs a new observer
    } else {
      teardownInfiniteScroll();
    }
    updateLoadMoreHint(data.has_more, data.offset, data.results.length);
  } catch (err) {
    console.error("loadMore failed", err);
    showError();
  } finally {
    loadingMore = false;
    setLoading(false);
  }
}

function updateLoadMoreHint(hasMore, offset, count) {
  if (!loadMoreHint) return;
  if (hasMore) {
    loadMoreHint.textContent = "Scroll for more results.";
    loadMoreHint.classList.remove("load-more-hint--capped");
  } else if (count > 0) {
    // Reached the cap. The total is read from a data-attr on the hint
    // (set in the template), defaulting to 500.
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

function setLoading(on) {
  if (input) input.disabled = on;
  if (form) {
    const btn = form.querySelector("button");
    if (btn) btn.disabled = on;
  }
}

function showError() {
  if (resultCount) {
    resultCount.innerHTML = `<span class="state state-error">Search is currently unavailable. <a href="${escapeHtml(window.location.pathname + window.location.search)}">retry</a></span>`;
  }
  if (grid) grid.innerHTML = "";
}

// A page is "actively searching" when there's a text query, at least
// one positive prompt chip, OR an active `?centroid=` anchor. The
// centroid branch matters because the prompt UI is hidden in centroid
// mode (server-enforced mutex) — without this, the form's empty-query
// guard would redirect centroid pages to `/` and the infinite-scroll
// sentinel would never trigger a fetch. Filename-only mode is also a
// valid search anchor (the server resolves it to a zero-vector +
// HasId filter), so it qualifies too.
function hasActiveSearch(q) {
  if (q) return true;
  if (promptChips && promptChips.state.positives.length > 0) return true;
  if (readCentroid()) return true;
  if (readFavoritesFilter()) return true;
  if (readFilename()) return true;
  return false;
}

// Kept under the old name for the one callsite that already used it
// (form submit). New code should prefer hasActiveSearch() to keep the
// centroid branch in mind. The optional `filename` arg lets the
// form's submit handler see the freshly-typed value before the URL
// is updated (readFilename reads from the URL).
function hasPositivePrompt(q, filename = "") {
  if (hasActiveSearch(q)) return true;
  if (filename) return true;
  return false;
}

function showPromptError(message) {
  if (!promptError) return;
  promptError.textContent = message;
  promptError.hidden = false;
}

function clearPromptError() {
  if (!promptError) return;
  promptError.textContent = "";
  promptError.hidden = true;
}

// ── View dispatcher + toggle ─────────────────────────────────────────────

// Map a `?view=` value to a renderer pair. The two renderers share an
// API: `render(rootEl, results, opts)` and `append(rootEl, results)`.
function getRenderer(view) {
  if (view === "feed") {
    return { render: renderFeed, append: appendToFeed };
  }
  return { render: renderGrid, append: appendToGrid };
}

function syncViewToggle() {
  const current = readView();
  for (const btn of document.querySelectorAll(".view-toggle-btn")) {
    const isActive = btn.dataset.view === current;
    btn.classList.toggle("view-toggle-btn--active", isActive);
    btn.setAttribute("aria-pressed", isActive ? "true" : "false");
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
  history.pushState({ q: readQuery() }, "", url.pathname + (url.search || ""));
  syncViewToggle();
  // Re-render current results with the new view — no new fetch needed,
  // the data is the same. Falls back to a fresh search on the empty
  // landing page (no results to re-render yet).
  if (grid && grid.children.length > 0) {
    const renderer = getRenderer(nextView);
    // Capture the current results from the DOM. The grid renderer
    // stored the most recent payload in `grid.dataset`; we don't keep
    // the full result array around, so the cleanest path is to
    // re-fetch the first page. Cheap: the data is in Qdrant's HNSW
    // and the network round trip is small.
    runSearch();
  }
}

// Set the toggle's active state on initial load, and wire up clicks.
syncViewToggle();
for (const btn of document.querySelectorAll(".view-toggle-btn")) {
  btn.addEventListener("click", () => onViewToggleClick(btn.dataset.view));
}

// ── Diversity toggle ────────────────────────────────────────────────────
//
// A simple on/off toggle that adds `?diverse=true` to the URL and
// re-runs the search with MMR re-ranking. Mirrors the view-toggle
// pattern (URL round-trip, shareable, back-button-aware).

function syncDiverseToggle() {
  const on = readDiverse();
  for (const btn of document.querySelectorAll("[data-diverse]")) {
    btn.classList.toggle("diverse-btn--active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
}

function onDiverseToggleClick() {
  const nextState = !readDiverse();
  const url = new URL(window.location.href);
  if (nextState) {
    url.searchParams.set("diverse", "true");
  } else {
    url.searchParams.delete("diverse");
  }
  history.pushState({ q: readQuery() }, "", url.pathname + (url.search || ""));
  syncDiverseToggle();
  // Re-run the search if we have an active query. Falls back to a
  // fresh SSR page on the empty landing page.
  if (hasPositivePrompt(readQuery())) {
    runSearch();
  }
}

syncDiverseToggle();
for (const btn of document.querySelectorAll("[data-diverse]")) {
  btn.addEventListener("click", onDiverseToggleClick);
}

// Keep the toggle in sync with browser back/forward navigation.
// (The main popstate listener above already calls runSearch(); the
// toggle is just a UI affordance, so we sync its visual state there
// too — no extra fetch needed.)

// ── Surprise Me button ────────────────────────────────────────────────
//
// Clicking the button fires a search with `surprise=true` in the API
// call but does NOT change the browser URL. Every click fetches a new
// random sample from the deep Qdrant pool.

function onSurpriseClick() {
  setLoading(true);
  // Read from the input field (not the URL) so typed-but-unsubmitted
  // text is picked up. The same pattern `runSearch` uses at line 85.
  const q = input ? input.value.trim() : readQuery().trim();
  const apiUrl = buildApiUrl(q, true);  // surprise=true
  fetch(apiUrl, { headers: { Accept: "application/json" } })
    .then(r => r.json())
    .then(data => {
      renderInitial(data);
      syncChipActiveState();
    })
    .catch(() => showError())
    .finally(() => setLoading(false));
}

for (const btn of document.querySelectorAll("[data-surprise]")) {
  btn.addEventListener("click", onSurpriseClick);
}

// ── Infinite scroll ────────────────────────────────────────────────

function attachInfiniteScroll() {
  if (!grid) return;
  teardownInfiniteScroll();
  const sentinel = grid.querySelector(":scope > .grid-sentinel");
  if (!sentinel) return;
  if (!("IntersectionObserver" in window)) return;
  io = new IntersectionObserver(
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
  io.observe(sentinel);
}

function teardownInfiniteScroll() {
  if (io) {
    io.disconnect();
    io = null;
  }
}

// ── Library chip filter ──────────────────────────────────────────────────

async function populateCollectionChips() {
  if (!categoryBar) return;
  let collections;
  try {
    const resp = await fetch("/api/collections", {
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) return;
    const data = await resp.json();
    collections = Array.isArray(data?.collections) ? data.collections : [];
  } catch (err) {
    return;
  }
  if (collections.length === 0) {
    categoryBar.innerHTML = "";
    return;
  }
  // Sort by name (the endpoint already sorts, but be defensive).
  collections.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  const active = new Set(activeCollections());
  const frag = document.createDocumentFragment();
  for (const { name, count } of collections) {
    if (!name) continue;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.dataset.collection = name;
    btn.setAttribute("aria-pressed", active.has(name) ? "true" : "false");
    if (active.has(name)) btn.classList.add("chip--active");
    btn.innerHTML =
      `<span class="chip-label">${escapeHtml(name)}</span>` +
      `<span class="chip-count">${Number(count) || 0}</span>`;
    btn.addEventListener("click", () => onChipClick(name));
    frag.appendChild(btn);
  }
  categoryBar.innerHTML = "";
  categoryBar.appendChild(frag);
}

function syncChipActiveState() {
  if (!categoryBar) return;
  const active = new Set(activeCollections());
  for (const btn of categoryBar.querySelectorAll(".chip")) {
    const on = active.has(btn.dataset.collection);
    btn.classList.toggle("chip--active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
}

function updateFilterSummary() {
  if (!filterSummary) return;
  const promptCount = promptChips
    ? promptChips.state.positives.length + promptChips.state.negatives.length
    : 0;
  const filterCount = promptCount + (readFilename() ? 1 : 0) + activeCollections().length;
  filterSummary.textContent = filterCount
    ? `${filterCount} active`
    : "Optional";
}

promptRoot?.addEventListener("promptschanged", updateFilterSummary);
filenameInput?.addEventListener("input", updateFilterSummary);
updateFilterSummary();

function onChipClick(name) {
  const url = new URL(window.location.href);
  // Toggle: remove if present, add if absent. We model active set as
  // a Set, then rewrite the URL with sorted-deduped values for
  // stable back/forward navigation.
  const current = new Set(url.searchParams.getAll("collection"));
  if (current.has(name)) current.delete(name);
  else current.add(name);
  url.searchParams.delete("collection");
  for (const c of [...current].sort()) {
    url.searchParams.append("collection", c);
  }
  const newPath = url.pathname + (url.search || "");
  history.pushState({ q: readQuery() }, "", newPath);
  syncChipActiveState();
  // Re-run the search if we have an active query. On the landing
  // page (no q) just sit on the empty state — the URL change is
  // reflected in the chip's active state for when the user types.
  if (hasPositivePrompt(readQuery())) {
    runSearch();
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ---------------- Saved searches ----------------
//
// Wires the Saved dropdown + Save current button rendered into the
// search bar. Behaviour:
//   - Selecting a saved search clears the current chips and repopulates
//     from the option's data attributes, then submits the form so the
//     search runs immediately. Matches the chip data attributes the
//     PromptChips controller reads, so this stays in lockstep with
//     how chips are normally typed.
//   - Save current prompts the user for a name (default derived from
//     the chips: first positive, or `first_pos −first_neg` if any
//     negatives are set, capped at 50 chars), POSTs to
//     /api/saved-searches, and on success refreshes the dropdown so
//     the new entry is selectable without a page reload.
//   - Save button is enabled only when at least one positive OR
//     negative chip exists — empty saves are rejected both client-
//     and server-side.
const savedBar = document.querySelector("[data-saved-bar]");
if (savedBar) {
  const select = savedBar.querySelector("[data-saved-select]");
  const saveBtn = savedBar.querySelector("[data-saved-save]");
  const errorEl = savedBar.querySelector("[data-saved-error]");

  const showSavedError = (msg) => {
    if (!errorEl) return;
    errorEl.textContent = msg || "";
    if (msg) errorEl.removeAttribute("hidden");
    else errorEl.setAttribute("hidden", "");
  };

  // Dropdown apply. Hydrate chips from the selected option's
  // data-saved-positives / data-saved-negatives attributes, then
  // submit the form. Re-render the chips first so the user sees the
  // transition before the search kicks off.
  if (select) {
    select.addEventListener("change", () => {
      const option = select.options[select.selectedIndex];
      if (!option || !option.value) return;
      let pos = [];
      let neg = [];
      try {
        pos = JSON.parse(option.dataset.savedPositives || "[]") || [];
      } catch (_) { pos = []; }
      try {
        neg = JSON.parse(option.dataset.savedNegatives || "[]") || [];
      } catch (_) { neg = []; }
      if (promptChips) {
        promptChips.clear("positives");
        promptChips.clear("negatives");
        for (const p of pos) promptChips.add("positives", p);
        for (const n of neg) promptChips.add("negatives", n);
      }
      showSavedError("");
      // Trigger the form's submit path so the search actually runs.
      // Same handler used by the q-input submit listener above.
      if (form) form.requestSubmit();
    });
  }

  // Save current prompts → POST → refresh dropdown.
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      if (!promptChips) return;
      const positives = [...promptChips.state.positives];
      const negatives = [...promptChips.state.negatives];
      if (!positives.length && !negatives.length) {
        showSavedError("Type at least one prompt first.");
        return;
      }
      const firstPos = positives[0] || "";
      const firstNeg = negatives[0] || "";
      let defaultName = firstPos;
      if (firstNeg) defaultName = firstNeg ? `${firstPos} -${firstNeg}` : firstPos;
      if (defaultName.length > 50) defaultName = defaultName.slice(0, 50);
      const name = (window.prompt("Name this saved search:", defaultName) || "").trim();
      if (!name) return;
      showSavedError("");
      saveBtn.disabled = true;
      try {
        const resp = await fetch("/api/saved-searches", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ name, positives, negatives }),
        });
        if (!resp.ok) {
          const detail = resp.status === 409 ? "Name already in use." : `Save failed (${resp.status}).`;
          showSavedError(detail);
          return;
        }
        const saved = await resp.json();
        // Insert the new option (dedupe by id), select it, and reset
        // the form back to the default option so the user can pick a
        // different saved search next.
        const opt = document.createElement("option");
        opt.value = String(saved.id);
        opt.textContent = saved.name;
        opt.dataset.savedPositives = JSON.stringify(saved.positives || []);
        opt.dataset.savedNegatives = JSON.stringify(saved.negatives || []);
        // Remove any pre-existing option with the same id (re-save flow).
        for (const existing of Array.from(select.options)) {
          if (existing.value === opt.value) existing.remove();
        }
        // Insert after the default placeholder (index 0) so "— pick —"
        // stays on top.
        select.insertBefore(opt, select.options[1] || null);
        select.selectedIndex = 0;
      } catch (e) {
        showSavedError("Network error.");
      } finally {
        updateSaveButtonState();
      }
    });
  }

  const updateSaveButtonState = () => {
    if (!saveBtn || !promptChips) return;
    const hasAny =
      promptChips.state.positives.length > 0 ||
      promptChips.state.negatives.length > 0;
    saveBtn.disabled = !hasAny;
    if (hasAny) saveBtn.removeAttribute("title");
    else saveBtn.setAttribute("title", "Type at least one prompt to save");
  };

  // Initial state + listen for chip changes so the Save button lights
  // up the moment the user has anything in the form.
  updateSaveButtonState();
  if (promptChips) {
    const origRender = promptChips.render.bind(promptChips);
    promptChips.render = function () {
      origRender();
      updateSaveButtonState();
    };
  }
}
