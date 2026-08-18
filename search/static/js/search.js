// search.js — page-level controller
//
// Behavior contract:
//   - The form owns a draft search. Include/Exclude prompts, collections,
//     filename, Diversity, depth, and view changes do not touch the URL or
//     fetch results.
//   - Clicking Search commits the draft to the URL and starts one search.
//     This is the only path for ordinary search controls to start a search.
//   - popstate restores the committed URL state and re-runs that committed
//     search so browser navigation remains useful.
//   - Infinite scroll reads only committed URL state; unfinished edits cannot
//     leak into another page of the current result set.
//   - Library chips are metadata-loaded once from /api/collections, then
//     toggled locally until Search is clicked.

import { buildSearchUrlWithFilename, readCentroid, readCentroidWeights, readCentroids, readCollections, readDiversityDepth, readDiversityMode, readFavoritesFilter, readFilename, readPrompts, readQuery, readView } from "./lib/url.js"
import { renderGrid, appendToGrid, addSentinel, removeSentinel } from "./lib/grid.js";
import { renderFeed, appendToFeed } from "./lib/feed.js";
import { PromptChips } from "./lib/prompts.js";

const form = document.querySelector(".search-form");
const filenameInput = form?.querySelector('input[name="filename"]');
const resultCount = document.querySelector(".result-count");
const grid = document.getElementById("result-grid");
const loadMoreHint = document.querySelector(".load-more-hint");
const categoryBar = document.getElementById("category-bar");
const promptRoot = document.querySelector(".prompt-composition");
const promptError = document.querySelector(".prompt-error");
const filterSummary = document.querySelector("[data-filter-summary]");
const draftStatus = document.querySelector("[data-search-draft-status]");
const submitButton = form?.querySelector(".search-submit");

const draftState = {
  collections: new Set(readCollections()),
  diversityMode: readDiversityMode(),
  diversityDepth: readDiversityDepth(),
  filename: readFilename(),
  view: readView(),
};

let syncingDraft = false;
let draftDirty = false;
const promptChips = promptRoot
  ? new PromptChips(promptRoot, initialPromptState())
  : null;

if (submitButton?.disabled) submitButton.dataset.locked = "true";

let loadingMore = false;
let loadingSearch = false;
let searchGeneration = 0;
let searchController = null;
let loadMoreGeneration = 0;
let loadMoreController = null;
let io = null;

// Populate the library chip filter from /api/collections. This metadata
// request is independent of image search. Chip selections themselves stay
// in draftState until the user clicks Search.
populateCollectionChips();

if (form) {
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (submitButton?.dataset.locked === "true") return;
    flushPendingPromptInputs();
    if (!hasDraftSearch()) {
      showPromptError("Add at least one Include prompt or filename.");
      return;
    }
    clearPromptError();
    // The URL is the committed search. Draft controls have not changed it
    // while the user was configuring the search, so this is the single gate
    // where a new result request is allowed to begin.
    const url = buildSearchUrlWithFilename(
      "",
      promptChips?.serialize(),
      activeCollections(),
      draftState.filename,
      draftState.view,
      draftState.diversityMode,
      draftState.diversityDepth,
    );
    draftDirty = false;
    updateDraftStatus();
    history.pushState({ search: url }, "", url);
    if (!grid || !resultCount) {
      window.location.href = url;
      return;
    }
    runSearch();
  });
}

window.addEventListener("popstate", () => {
  syncDraftFromCommitted();
  syncViewToggle();
  syncDiversityControls();
  runSearch();
});

// Infinite scroll setup. Only attaches when the initial page rendered
// with has_more=true (i.e. a sentinel <li> is in the DOM).
attachInfiniteScroll();

async function runSearch() {
  cancelSearchRequest();
  invalidateLoadMore();
  const q = readQuery().trim();
  if (!hasActiveSearch(q)) {
    // Empty query: full reload to the bare / page. This keeps the
    // server-rendered landing state visible. Avoid reloading an already
    // empty landing page when popstate is restoring `/`.
    if (window.location.search || grid?.children.length) {
      window.location.href = "/";
    }
    return;
  }

  const requestGeneration = searchGeneration;
  const controller = new AbortController();
  searchController = controller;
  loadingSearch = true;
  setLoading(true);

  try {
    const apiUrl = buildApiUrl(q);
    const resp = await fetch(apiUrl, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (requestGeneration !== searchGeneration || controller.signal.aborted) return;
    if (!resp.ok) {
      showError();
      return;
    }
    const data = await resp.json();
    if (requestGeneration !== searchGeneration || controller.signal.aborted) return;
    renderInitial(data);
    // The committed URL is the source of truth after Search.
    syncChipActiveState();
  } catch (err) {
    if (controller.signal.aborted || requestGeneration !== searchGeneration) return;
    console.error("search failed", err);
    showError();
  } finally {
    if (searchController === controller) {
      searchController = null;
      loadingSearch = false;
      setLoading(false);
    }
  }
}

function cancelSearchRequest() {
  searchGeneration += 1;
  searchController?.abort();
  searchController = null;
  loadingSearch = false;
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
  for (const [key, value] of Object.entries(readPrompts())) {
    for (const prompt of value) {
      params.append(key, prompt);
    }
  }
  // Multi-value: every committed collection adds a `collection` param. The
  // server applies them as a MatchAny filter.
  for (const c of readCollections()) {
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
  const diversityMode = readDiversityMode();
  if (diversityMode !== "off") {
    params.set("diversity", diversityMode);
    const diversityDepth = readDiversityDepth();
    if (diversityDepth !== "auto") {
      params.set("diversity_depth", diversityDepth);
    }
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

function buildDraftApiUrl(surprise = false) {
  const committedUrl = buildSearchUrlWithFilename(
    "",
    promptChips?.serialize(),
    activeCollections(),
    draftState.filename,
    draftState.view,
    draftState.diversityMode,
    draftState.diversityDepth,
  );
  const url = new URL(committedUrl, window.location.origin);
  if (surprise) url.searchParams.set("surprise", "true");
  return `/api/search${url.search}`;
}

function activeCollections() {
  return [...draftState.collections].sort();
}

function initialPromptState() {
  const prompts = readPrompts();
  const legacyQuery = readQuery().trim();
  if (legacyQuery && !prompts.positives.some((p) => p.toLowerCase() === legacyQuery.toLowerCase())) {
    prompts.positives.unshift(legacyQuery);
  }
  return prompts;
}

function committedPromptState() {
  return readPrompts();
}

function syncDraftFromCommitted() {
  syncingDraft = true;
  draftState.collections = new Set(readCollections());
  draftState.diversityMode = readDiversityMode();
  draftState.diversityDepth = readDiversityDepth();
  draftState.filename = readFilename();
  draftState.view = readView();
  if (filenameInput) filenameInput.value = draftState.filename;
  promptChips?.hydrate(initialPromptState());
  promptChips?.render();
  syncingDraft = false;
  draftDirty = false;
  updateDraftStatus();
  updateFilterSummary();
}

function markDraftDirty() {
  if (syncingDraft) return;
  invalidateLoadMore();
  draftDirty = true;
  updateDraftStatus();
}

function updateDraftStatus() {
  if (!draftStatus) return;
  const submitLabel = draftDirty ? "Changes ready — click Search." : "Set your prompts and filters, then click Search.";
  draftStatus.textContent = submitLabel;
  draftStatus.classList.toggle("is-dirty", draftDirty);
  submitButton?.classList.toggle("is-dirty", draftDirty);
  form?.classList.toggle("search-form--dirty", draftDirty);
}

function flushPendingPromptInputs() {
  if (!promptRoot || !promptChips) return;
  for (const side of ["positives", "negatives"]) {
    const input = promptRoot.querySelector(`[data-prompt-input="${side}"]`);
    const rawValue = input?.value || "";
    if (rawValue.trim()) promptChips.add(side, rawValue);
    // Clear even duplicate prompts: the draft has already normalized them,
    // and leaving the rejected text visible makes Search appear incomplete.
    if (rawValue) input.value = "";
  }
}

function hasDraftSearch() {
  if (promptChips?.state.positives.length) return true;
  if (draftState.filename.trim()) return true;
  if (readCentroid()) return true;
  return false;
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
  if (loadingMore || loadingSearch || !grid || draftDirty) return;
  const q = readQuery().trim();
  const filename = readFilename();
  if (!hasActiveSearch(q)) return;
  const currentOffset = Number(grid.dataset.offset || "0");
  const limit = Number(grid.dataset.limit || "35");
  const nextOffset = currentOffset;

  loadingMore = true;
  const requestGeneration = loadMoreGeneration;
  const controller = new AbortController();
  loadMoreController = controller;
  setLoading(true);
  try {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    // Same forwarding rule as buildApiUrl — without this, the API
    // would 400 ("at least one positive prompt is required") and the
    // sentinel would just sit there as the user scrolls.
    for (const centroid of readCentroids()) {
      params.append("centroid", centroid);
    }
    const weights = readCentroidWeights();
    if (weights && readCentroids().length > 1 && weights.length === readCentroids().length) {
      params.set("weights", weights.join(","));
    }
    if (filename) params.set("filename", filename);
    params.set("offset", String(nextOffset));
    params.set("limit", String(limit));
    for (const [key, value] of Object.entries(committedPromptState())) {
      for (const prompt of value) {
        params.append(key, prompt);
      }
    }
    for (const c of readCollections()) {
      params.append("collection", c);
    }
    if (readFavoritesFilter()) {
      params.set("favorites", "true");
    }
    const diversityMode = readDiversityMode();
    if (diversityMode !== "off") {
      params.set("diversity", diversityMode);
      const diversityDepth = readDiversityDepth();
      if (diversityDepth !== "auto") {
        params.set("diversity_depth", diversityDepth);
      }
    }
    const view = readView();
    if (view && view !== "grid") {
      params.set("view", view);
    }
    const resp = await fetch(`/api/search?${params.toString()}`, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (requestGeneration !== loadMoreGeneration || controller.signal.aborted) return;
    if (!resp.ok) {
      showError();
      return;
    }
    const data = await resp.json();
    if (requestGeneration !== loadMoreGeneration || controller.signal.aborted) return;
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
    if (controller.signal.aborted || requestGeneration !== loadMoreGeneration) return;
    console.error("loadMore failed", err);
    showError();
  } finally {
    if (loadMoreController === controller) {
      loadMoreController = null;
    }
    if (requestGeneration === loadMoreGeneration) {
      loadingMore = false;
      setLoading(false);
    }
  }
}

function invalidateLoadMore() {
  const wasLoading = loadingMore || Boolean(loadMoreController);
  loadMoreGeneration += 1;
  loadMoreController?.abort();
  loadMoreController = null;
  loadingMore = false;
  if (wasLoading && !loadingSearch) setLoading(false);
}

function updateLoadMoreHint(hasMore, offset, count) {
  if (!loadMoreHint) return;
  if (hasMore) {
    loadMoreHint.textContent = "Scroll for more results.";
  } else {
    // Reached the cap (or empty result set) — the page has nothing
    // more to show. No narration: the empty state speaks for itself.
    loadMoreHint.textContent = "";
  }
}

function setLoading(on) {
  if (!submitButton) return;
  if (on) {
    submitButton.disabled = true;
    submitButton.setAttribute("aria-busy", "true");
    submitButton.classList.add("is-loading");
  } else {
    submitButton.removeAttribute("aria-busy");
    submitButton.classList.remove("is-loading");
    submitButton.disabled = submitButton.dataset.locked === "true";
  }
}

function showError() {
  if (resultCount) {
    resultCount.innerHTML = `<span class="state state-error">Search is currently unavailable. <a href="${escapeHtml(window.location.pathname + window.location.search)}">retry</a></span>`;
  }
  if (grid) grid.innerHTML = "";
}

// A committed page is "actively searching" when there's a text query, at
// least one positive prompt, an active centroid anchor, or a filename-only
// browse. This reads only the URL; draft prompts must not make the current
// result grid start searching before the Search button is clicked.
function hasActiveSearch(q) {
  if (q) return true;
  if (readPrompts().positives.length > 0) return true;
  if (readCentroid()) return true;
  if (readFavoritesFilter()) return true;
  if (readFilename()) return true;
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
  const current = draftState.view;
  for (const btn of document.querySelectorAll(".segmented-btn")) {
    const isActive = btn.dataset.view === current;
    btn.classList.toggle("is-active", isActive);
    btn.setAttribute("aria-pressed", isActive ? "true" : "false");
  }
}

function onViewToggleClick(nextView) {
  if (draftState.view === nextView) return;
  draftState.view = nextView;
  syncViewToggle();
  markDraftDirty();
}

// Set the toggle's active state on initial load, and wire up clicks.
syncViewToggle();
for (const btn of document.querySelectorAll(".segmented-btn")) {
  btn.addEventListener("click", () => onViewToggleClick(btn.dataset.view));
}

// ── Diversity controls ────────────────────────────────────────────────
//
// Diversity remains a search mode, but its strength is explicit in the URL
// so a saved/shared search communicates how it was ranked.

const diversitySelect = document.querySelector("[data-diversity-select]");
const diversityDepthSelect = document.querySelector("[data-diversity-depth-select]");

function syncDiversityControls() {
  if (diversitySelect) diversitySelect.value = draftState.diversityMode;
  if (diversityDepthSelect) {
    diversityDepthSelect.value = draftState.diversityDepth;
    diversityDepthSelect.disabled = draftState.diversityMode === "off";
  }
}

function onDiversityChange() {
  const nextMode = diversitySelect?.value || "off";
  draftState.diversityMode = nextMode;
  syncDiversityControls();
  markDraftDirty();
}

function onDiversityDepthChange() {
  const nextDepth = diversityDepthSelect?.value || "auto";
  draftState.diversityDepth = nextDepth;
  syncDiversityControls();
  markDraftDirty();
}

syncDiversityControls();
diversitySelect?.addEventListener("change", onDiversityChange);
diversityDepthSelect?.addEventListener("change", onDiversityDepthChange);

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
  flushPendingPromptInputs();
  if (!hasDraftSearch()) {
    showPromptError("Add at least one Include prompt or filename.");
    return;
  }
  clearPromptError();
  setLoading(true);
  const apiUrl = buildDraftApiUrl(true);
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
  const filterCount = (draftState.filename ? 1 : 0) + activeCollections().length;
  filterSummary.textContent = filterCount
    ? `${filterCount} active`
    : "Optional";
}

promptRoot?.addEventListener("promptschanged", () => {
  if (!syncingDraft) markDraftDirty();
  updateFilterSummary();
});
filenameInput?.addEventListener("input", () => {
  draftState.filename = filenameInput.value.trim();
  markDraftDirty();
  updateFilterSummary();
});
for (const input of promptRoot?.querySelectorAll("[data-prompt-input]") || []) {
  input.addEventListener("input", markDraftDirty);
}
updateFilterSummary();

function onChipClick(name) {
  if (draftState.collections.has(name)) draftState.collections.delete(name);
  else draftState.collections.add(name);
  syncChipActiveState();
  markDraftDirty();
  updateFilterSummary();
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
//     from the option's data attributes. It only changes the draft; the
//     user must still click Search to commit it.
//   - Save current prompts the user for a name (default derived from
//     the chips: first positive, or `first_pos −first_neg` if any
//     negatives are set, capped at 50 chars), POSTs to
//     /api/saved-searches, and on success refreshes the dropdown so
//     the new entry is selectable without a page reload.
//   - Save button is enabled only when at least one Include chip exists;
//     a negative-only preset cannot produce a valid search.
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
  // data-saved-positives / data-saved-negatives attributes. This is a
  // draft-only action; Search remains the explicit commit gate.
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
      for (const input of promptRoot?.querySelectorAll("[data-prompt-input]") || []) {
        input.value = "";
      }
      showSavedError("");
      markDraftDirty();
    });
  }

  // Save current prompts → POST → refresh dropdown.
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      if (!promptChips) return;
      flushPendingPromptInputs();
      const positives = [...promptChips.state.positives];
      const negatives = [...promptChips.state.negatives];
      if (!positives.length) {
        showSavedError("Add at least one Include prompt first.");
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
    const includeInput = promptRoot?.querySelector('[data-prompt-input="positives"]');
    const hasInclude =
      promptChips.state.positives.length > 0 ||
      Boolean(includeInput?.value.trim());
    saveBtn.disabled = !hasInclude;
    if (hasInclude) saveBtn.removeAttribute("title");
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
  for (const input of promptRoot?.querySelectorAll("[data-prompt-input]") || []) {
    input.addEventListener("input", updateSaveButtonState);
  }
}
