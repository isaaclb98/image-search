// lib/url.js — query string helpers
//
// Tiny, no-dependency URL-state utilities. We use them to keep
// Legacy `?q=...`, multi-value prompt params, and collection filters in sync
// and to react to popstate. The search UI emits prompt params; `q` remains
// readable only for old links and API compatibility.

export function readQuery() {
  const params = new URLSearchParams(window.location.search);
  return params.get("q") || "";
}

// Active custom centroid(s) from `?centroid=<name>&centroid=<name>&weights=...`.
// The server treats any non-empty `?centroid=` list as a valid
// search anchor on its own (mutex with text prompts) — the client
// mirrors that so the scroll path forwards the full list back to
// /api/search and the "is there a real search?" guard
// (`hasActiveSearch`) recognizes centroid-only pages.
//
// Returns the full list (possibly empty). `weights` is parsed
// alongside — same comma/repeated-param shape as the server.
export function readCentroids() {
  const params = new URLSearchParams(window.location.search);
  return params.getAll("centroid").map((s) => s.trim()).filter(Boolean);
}

export function readCentroidWeights() {
  const params = new URLSearchParams(window.location.search);
  const raw = params.getAll("weights");
  if (raw.length === 0) return null;
  const flat = [];
  for (const r of raw) {
    for (const p of r.split(",")) {
      const t = p.trim();
      if (t) flat.push(t);
    }
  }
  if (flat.length === 0) return null;
  const parsed = flat.map(Number);
  if (parsed.some((n) => !Number.isFinite(n) || n <= 0)) return null;
  return parsed;
}

// Single-centroid convenience accessor. Returns the first centroid
// name when any are set, else "". Used by templates and code paths
// that haven't been migrated to the multi-centroid world yet.
export function readCentroid() {
  const list = readCentroids();
  return list.length ? list[0] : "";
}

export function readCategory() {
  const params = new URLSearchParams(window.location.search);
  return params.get("category") || "";
}

export function readPrompts() {
  const params = new URLSearchParams(window.location.search);
  return {
    positives: params.getAll("positives").map((s) => s.trim()).filter(Boolean),
    negatives: params.getAll("negatives").map((s) => s.trim()).filter(Boolean),
  };
}

export function readCollections() {
  const params = new URLSearchParams(window.location.search);
  return params.getAll("collection").filter(Boolean);
}

export function readFavoritesFilter() {
  const params = new URLSearchParams(window.location.search);
  return params.get("favorites") === "true";
}

// Filename/path-substring filter. Single-valued (?filename=...)
// for parity with the server's `_parse_filename`. Empty / whitespace
// returns "" so callers can do the standard "if not value: skip"
// check, and so the JS state mirror stays in lockstep with the URL.
export function readFilename() {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("filename") || "";
  return raw.trim();
}

// Result view. The server treats anything outside {"grid","feed"} as
// "grid" — the JS mirrors that fallback so the toggle UI and the rendered
// output always agree.
export function readDiverse() {
  return readDiversityMode() !== "off";
}

// Search-only Diversity mode. The legacy `?diverse=true` URL remains a
// backwards-compatible alias for `balanced`.
export function readDiversityMode() {
  const params = new URLSearchParams(window.location.search);
  const mode = (params.get("diversity") || "").toLowerCase();
  if (["low", "balanced", "high"].includes(mode)) return mode;
  if (mode === "off") return "off";
  // The legacy alias is only a fallback. An explicit `diversity=off` must
  // win, matching the server's resolve_mode contract.
  const legacy = (params.get("diverse") || "").toLowerCase();
  if (!mode && ["true", "1", "on", "yes", "y", "t"].includes(legacy)) {
    return "balanced";
  }
  return "off";
}

// Candidate-pool depth is independent from Diversity strength. Keep the
// public values stepped so URLs remain reproducible and the server can bound
// the expensive vector ranking pass.
export function readDiversityDepth() {
  const params = new URLSearchParams(window.location.search);
  const depth = (params.get("diversity_depth") || "").toLowerCase();
  if (["500", "1000", "2000", "5000"].includes(depth)) return depth;
  return "auto";
}


export function readView() {
  const params = new URLSearchParams(window.location.search);
  const v = params.get("view");
  if (v === "feed") return "feed";
  return "grid";
}

export function writeQuery(q) {
  const url = new URL(window.location.href);
  if (q) {
    url.searchParams.set("q", q);
  } else {
    url.searchParams.delete("q");
  }
  return url.pathname + (url.search || "");
}

export function buildSearchUrl(
  q, promptParams, collections = [], view = null, diversityMode = null,
  diversityDepth = null,
) {
  // Build the canonical search URL from scratch (so old junk params
  // don't leak through). View is opt-in: pass an explicit value, or
  // we'll preserve the current `?view=` from the URL. Passing
  // DEFAULT_VIEW ("grid") omits the param to keep URLs clean.
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (promptParams) {
    for (const [key, value] of promptParams.entries()) {
      params.append(key, value);
    }
  }
  for (const collection of collections) {
    params.append("collection", collection);
  }
  appendCentroidState(params);
  // Filename: round-trip from the URL so the state mirror stays
  // in sync with the server. Empty values are stripped to keep
  // canonical URLs clean (matching the server's behaviour).
  const filename = readFilename();
  if (filename) {
    params.set("filename", filename);
  }
  if (readFavoritesFilter()) {
    params.set("favorites", "true");
  }
  const effectiveDiversity = diversityMode === null ? readDiversityMode() : diversityMode;
  if (effectiveDiversity && effectiveDiversity !== "off") {
    params.set("diversity", effectiveDiversity);
    const effectiveDepth = diversityDepth === null ? readDiversityDepth() : diversityDepth;
    if (effectiveDepth && effectiveDepth !== "auto") {
      params.set("diversity_depth", effectiveDepth);
    }
  }
  const effectiveView = view === null ? readView() : view;
  if (effectiveView && effectiveView !== "grid") {
    params.set("view", effectiveView);
  }
  const query = params.toString();
  return `/${query ? `?${query}` : ""}`;
}

// Centroid searches are already committed in the current URL when the
// user changes a search-only control such as Diversity. Preserve those
// anchors while rebuilding the URL so clicking Search does not silently
// turn a centroid search into an empty text search.
function appendCentroidState(params) {
  const centroids = readCentroids();
  for (const centroid of centroids) {
    params.append("centroid", centroid);
  }
  const weights = readCentroidWeights();
  if (weights && centroids.length > 1 && weights.length === centroids.length) {
    params.set("weights", weights.join(","));
  }
}

// Like buildSearchUrl but lets the caller pass an explicit filename
// (the form's current input value) rather than reading from the
// URL. The form submit handler needs this because the user's typed
// value isn't in `window.location` yet — readFilename would see the
// stale URL. Pass null to clear the filter.
export function buildSearchUrlWithFilename(
  q, promptParams, collections, filename, view, diversityMode = null,
  diversityDepth = null,
) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (promptParams) {
    for (const [key, value] of promptParams.entries()) {
      params.append(key, value);
    }
  }
  for (const collection of collections) {
    params.append("collection", collection);
  }
  appendCentroidState(params);
  if (filename) {
    params.set("filename", filename);
  }
  if (readFavoritesFilter()) {
    params.set("favorites", "true");
  }
  const effectiveDiversity = diversityMode === null ? readDiversityMode() : diversityMode;
  if (effectiveDiversity && effectiveDiversity !== "off") {
    params.set("diversity", effectiveDiversity);
    const effectiveDepth = diversityDepth === null ? readDiversityDepth() : diversityDepth;
    if (effectiveDepth && effectiveDepth !== "auto") {
      params.set("diversity_depth", effectiveDepth);
    }
  }
  const effectiveView = view === null ? readView() : view;
  if (effectiveView && effectiveView !== "grid") {
    params.set("view", effectiveView);
  }
  const query = params.toString();
  return `/${query ? `?${query}` : ""}`;
}
