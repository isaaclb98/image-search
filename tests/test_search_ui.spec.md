# UI Test Specification

**Status:** This file is the canonical UI test specification.

**How to use in v1:** Work through each case manually in a browser,
marking pass/fail.

**How to use in v1.1+:** Cases tagged `[AUTO]` map directly to
Playwright tests in `tests/test_search_ui.py`. Cases tagged
`[MANUAL]` remain checklist-only.

---

## UI test cases — full specification

This is the spec. IDs are stable so test runners (Playwright or human) can reference them. `Given`/`When`/`Then` is the structure. **Items marked `[AUTO]` are the ones Playwright tests should cover when added. Items marked `[MANUAL]` are checklist-only in v1.**

### Group A — search page (`GET /`)

#### UI-S-001 — initial page load, no query `[AUTO]`

- **Given:** the search page is opened for the first time.
- **When:** the browser loads `GET /`.
- **Then:**
  - HTTP 200.
  - URL has no `?q=`.
  - Page contains a `<form>` with Include/Exclude prompt inputs and a Search button.
  - The legacy `Search your library…` input is absent.
  - Page does **not** show a "results" or "no results" message.
  - The Include prompt input is autofocused.
  - No network requests to `/api/search` are made (the page is fully server-rendered for the empty state). The collections metadata request is allowed.

#### UI-S-002 — initial page load with a committed Include prompt `[AUTO]`

- **Given:** Qdrant contains 3+ indexed images; some match "cat," some do not.
- **When:** the browser loads `GET /?positives=cat`.
- **Then:**
  - HTTP 200.
  - The Include chip has value `cat`.
  - The result count text is visible (e.g., "Showing N results for 'cat'").
  - The grid renders 1+ `<li>` items, each containing a link to `/photo/<id>` and an `<img src="/photo/<id>/raw">`.
  - The Include prompt input is **not** autofocused (we don't want the cursor jumping after a search).

#### UI-S-003 — Search button commits the draft `[AUTO]`

- **Given:** the user is on `GET /` with no query.
- **When:** the user types `cat` in Include and clicks Search.
- **Then:**
  - Browser navigates to `GET /?positives=cat`.
  - The result grid renders.
  - URL bar shows `?positives=cat`.

#### UI-S-004 — editing controls does not search `[AUTO]`

- **Given:** the user is on a committed result page.
- **When:** the user changes a collection or Diversity setting.
- **Then:** the URL, result grid, and `/api/search` request count remain unchanged.
- **When:** the user clicks Search.
- **Then:** one new committed URL and one new search are produced.

#### UI-S-004a — saved search selection is draft-only `[AUTO]`

- **Given:** the user is on a committed result page.
- **When:** the user selects a saved search.
- **Then:** Include/Exclude chips update, but the URL, result grid, and search request remain unchanged until Search is clicked.

#### UI-S-004b — unfinished edits cannot append stale pages `[AUTO]`

- **Given:** an infinite-scroll request is in flight for the current committed search.
- **When:** the user changes a filter or submits a new search before that request returns.
- **Then:** the old response is ignored or aborted and cannot append results to the new committed result grid.

#### UI-S-005 — empty Include submission `[AUTO]`

- **Given:** the user is on `GET /`.
- **When:** the user clicks Search with no Include prompt or filename.
- **Then:**
  - The form shows an inline Include error and does not navigate.
  - No API request is made.

#### UI-S-006 — whitespace-only Include prompt `[AUTO]`

- **Given:** the user is on `GET /`.
- **When:** the user types `   ` (only whitespace) in Include and clicks Search.
- **Then:**
  - The form shows an inline Include error and remains on the current URL.
  - No API request is made.

#### UI-S-007 — long Include prompt (over 512 chars) `[AUTO]`

- **Given:** the user is on `GET /`.
- **When:** the user pastes a 600-character string into Include and clicks Search.
- **Then:**
  - The API returns 400 `bad_request`.
  - The page renders the error state.
  - No Qdrant query is executed.

#### UI-S-008 — Include prompt with URL-unsafe characters `[AUTO]`

- **Given:** the user types `cat & dog`.
- **When:** the user submits.
- **Then:**
  - The browser navigates to `GET /?positives=cat+%26+dog` (or similar correctly-encoded URL).
  - The Include chip decodes back to `cat & dog` on the rendered page.
  - Search executes with the decoded query.

#### UI-S-009 — query with quotes/special chars `[AUTO]`

- **Given:** the user types `"best cat"`.
- **When:** the user submits.
- **Then:**
  - The query is treated as a literal string. SigLIP2 does the semantic work.
  - Search executes successfully.
  - Result count is reasonable (> 0 if any image semantically matches, 0 if not).

#### UI-S-010 — empty result set `[AUTO]`

- **Given:** a query that matches no images in the collection.
- **When:** the user submits.
- **Then:**
  - HTTP 200.
  - The page renders `No results for "<q>".`
  - The grid `<ul>` is empty.
  - No error state is shown.

#### UI-S-011 — large result set (top-K boundary) `[AUTO]`

- **Given:** a query that matches > `TOP_K_DEFAULT` images.
- **When:** the user submits.
- **Then:**
  - The page renders exactly `TOP_K_DEFAULT` results.
  - No "load more" button is shown (v1 has no pagination).

### Group B — result grid rendering

#### UI-G-001 — thumbnail lazy loading `[AUTO]`

- **Given:** the result grid is rendered with 50 thumbnails.
- **When:** the page is scrolled to the bottom.
- **Then:**
  - All 50 `<img>` elements are in the DOM.
  - Off-screen images load after they scroll into view (via native `loading="lazy"`).
  - On a fast connection, all images load within ~2 seconds.

#### UI-G-002 — broken image fallback `[AUTO]`

- **Given:** one of the result images points to a file that has been deleted from disk.
- **When:** the page renders the grid.
- **Then:**
  - The broken image shows a CSS background fallback (gray box) instead of the browser's broken-image icon.
  - The surrounding `<a href="/photo/<id>">` link is still clickable.
  - Clicking it navigates to the detail page, which shows the "file not found" notice.

#### UI-G-003 — thumbnail link navigation `[AUTO]`

- **Given:** the result grid is rendered.
- **When:** the user clicks any thumbnail.
- **Then:**
  - The browser navigates to `GET /photo/<id>`.
  - The detail page loads with the corresponding full image.

#### UI-G-004 — result order is stable `[AUTO]`

- **Given:** the user submits the same query twice in a row.
- **When:** both responses render.
- **Then:**
  - The result order is identical.
  - Scores are identical (visible in the page source if the dev wants to check).

### Group C — photo detail page (`GET /photo/{id}`)

#### UI-P-001 — valid photo id `[AUTO]`

- **Given:** a known photo id from the result grid.
- **When:** the browser loads `GET /photo/<id>`.
- **Then:**
  - HTTP 200.
  - Page contains a large `<img>` with `src="/photo/<id>/raw"`.
  - Page contains a metadata block: `path`, `indexed_at`, `model_name`.
  - Page contains a "← Back to results" link preserving the committed prompt URL.

#### UI-P-002 — unknown photo id `[AUTO]`

- **Given:** the user navigates to `GET /photo/does-not-exist`.
- **When:** the page loads.
- **Then:**
  - HTTP 404.
  - Page shows a `Photo not found.` notice (or similar).
  - No `<img>` is rendered.

#### UI-P-003 — malformed photo id `[AUTO]`

- **Given:** the user navigates to `GET /photo/...` or `GET /photo/%00`.
- **When:** the page loads.
- **Then:**
  - HTTP 404 or 400.
  - Page does not crash; a clear error notice is shown.

#### UI-P-004 — image file missing on disk `[AUTO]`

- **Given:** a known photo id, but the file has been deleted from the NAS.
- **When:** the page loads.
- **Then:**
  - HTTP 200.
  - The `<img>` tag is rendered, but the image returns 404.
  - The page shows a `File not found on disk.` notice.
  - Metadata is still visible.

#### UI-P-005 — back link works `[AUTO]`

- **Given:** the user came from `GET /?positives=cat&offset=50` (or whatever URL state is in use).
- **When:** the user clicks the "Back to results" link on the photo page.
- **Then:**
  - The browser navigates back to the committed prompt URL.
  - The result grid is re-rendered.

#### UI-P-006 — direct photo URL without prior search `[MANUAL]`

- **Given:** the user has never visited the search page.
- **When:** the user directly navigates to `GET /photo/<id>`.
- **Then:**
  - The page loads.
  - The "Back to results" link is either absent or links to `GET /` (no `?q=`).

#### UI-P-007 — raw image endpoint content type `[AUTO]`

- **Given:** a known photo id with a `.jpg` file on disk.
- **When:** the browser requests `GET /photo/<id>/raw`.
- **Then:**
  - HTTP 200.
  - `Content-Type: image/jpeg` (or detected from file content; PNG → `image/png`, etc.).
  - The response body is the raw image bytes.

### Group D — `/api/search` JSON API

#### UI-A-001 — happy path `[AUTO]`

- **Given:** a known query that matches 3+ images.
- **When:** the client requests `GET /api/search?q=cat`.
- **Then:**
  - HTTP 200.
  - `Content-Type: application/json`.
  - Body matches `SearchResponse`: `{query, results: [{id, path, score}, ...], took_ms}`.
  - `results` is non-empty; each `id` is a 32-char string; `score` is in `[-1, 1]` (cosine similarity range).

#### UI-A-002 — empty query `[AUTO]`

- **When:** the client requests `GET /api/search?q=`.
- **Then:** HTTP 400 with `ErrorResponse{code: "bad_request"}`.

#### UI-A-003 — missing query `[AUTO]`

- **When:** the client requests `GET /api/search` (no `q`).
- **Then:** HTTP 400 with `ErrorResponse{code: "bad_request"}`.

#### UI-A-004 — `limit` out of range `[AUTO]`

- **When:** the client requests `GET /api/search?q=cat&limit=0` or `limit=99999`.
- **Then:** HTTP 400 with `ErrorResponse{code: "bad_request"}`.

#### UI-A-005 — `limit` defaults `[AUTO]`

- **When:** the client requests `GET /api/search?q=cat` (no `limit`).
- **Then:** HTTP 200, `results.length == TOP_K_DEFAULT` (when the collection has at least that many matches).

#### UI-A-006 — Qdrant unreachable `[AUTO]`

- **Given:** the Qdrant container is stopped or the URL is wrong.
- **When:** the client requests `GET /api/search?q=cat`.
- **Then:** HTTP 502 with `ErrorResponse{code: "qdrant_unreachable"}`.

#### UI-A-007 — Qdrant timeout `[AUTO]`

- **Given:** Qdrant is healthy but slow (e.g., network latency injected).
- **When:** the client requests `GET /api/search?q=cat` with a 2s client timeout.
- **Then:** HTTP 504 with `ErrorResponse{code: "qdrant_timeout"}`.

#### UI-A-008 — long query `[AUTO]`

- **When:** the client requests `GET /api/search?q=<600-char string>`.
- **Then:** HTTP 400 with `ErrorResponse{code: "bad_request"}`.

#### UI-A-009 — result stability across requests `[AUTO]`

- **When:** the client makes the same request twice in a row.
- **Then:** the `id`s and `score`s in `results` are identical. Order is stable.

### Group E — browser navigation

#### UI-N-001 — back button after search `[AUTO]`

- **Given:** the user is on `GET /`.
- **When:** the user submits Include `cat`, lands on `?positives=cat`, then clicks a thumbnail to land on `/photo/<id>`.
- **Then:** clicking the browser back button returns to `?positives=cat` with the result grid re-rendered.

#### UI-N-002 — forward button `[AUTO]`

- **Given:** the user has gone back from `/photo/<id>` to `/?positives=cat`.
- **When:** the user clicks the browser forward button.
- **Then:** the browser returns to `/photo/<id>` with the detail page re-rendered.

#### UI-N-003 — URL state is shareable `[AUTO]`

- **Given:** the user is on `GET /?positives=cat`.
- **When:** the user copies the URL and opens it in a new tab.
- **Then:** the new tab shows the result grid for `cat` on first paint (no flash of empty state, no double-render).

#### UI-N-004 — JS popstate handler `[AUTO]`

- **Given:** the user is on `GET /?positives=cat` with JS enabled.
- **When:** the user navigates back from a result click and the result list re-renders.
- **Then:**
  - The result list re-renders without a full page reload.
  - The browser URL still shows `?positives=cat`.
  - `popstate` event fires; the JS handles it and re-runs the search.

#### UI-N-005 — direct deep link to query `[AUTO]`

- **Given:** the user has never visited the site.
- **When:** the user opens `GET /?positives=cat` directly.
- **Then:** the page renders fully on first paint (server-rendered, not JS-only). No flash of empty state.

### Group F — error and edge cases (UI-visible)

#### UI-E-001 — server returns 500 `[AUTO]`

- **Given:** the server is up but `/api/search` returns 500 (e.g., encoder OOM).
- **When:** the user submits a query.
- **Then:**
  - The page renders the error state (`Search is currently unavailable.`).
  - No technical detail is leaked to the page.
  - A "retry" link is visible.

#### UI-E-002 — network failure mid-search `[AUTO]`

- **Given:** the user submits a query and the network drops before the response.
- **When:** the request times out.
- **Then:**
  - The page renders the error state.
  - The form is re-enabled.
  - Re-submitting works.

#### UI-E-003 — Qdrant returns 0 results but collection has data `[AUTO]`

- **Given:** the collection has 1000 images but no semantic match for "purple elephant."
- **When:** the user submits.
- **Then:** `No results for "purple elephant".` renders. No error.

#### UI-E-004 — collection is empty `[AUTO]`

- **Given:** the Qdrant collection has 0 points.
- **When:** the user submits.
- **Then:** `No results for "<q>".` renders. (Per UI-E-003.) No error about empty collection.

#### UI-E-005 — image load timeout `[AUTO]`

- **Given:** a thumbnail's image is slow to load (>5s).
- **When:** the page renders.
- **Then:** the surrounding link is still clickable. The image eventually loads or the fallback engages.

#### UI-E-006 — extremely large image file `[MANUAL]`

- **Given:** one image in the collection is 50MB.
- **When:** the user clicks the thumbnail.
- **Then:** the detail page eventually loads. **Note:** this is a sanity check — if 50MB images are a regular case, we add a thumbnail endpoint in v1.1.

### Group G — performance & timing

#### UI-T-001 — cold-start page load `[AUTO]`

- **Given:** the search container has just started (no model in memory, no LRU cache hits).
- **When:** the user loads `GET /?positives=cat`.
- **Then:** total time to first paint < 5 seconds. (SigLIP2 text tower load is the bulk; this budget assumes it's been pre-loaded by the startup event.)

#### UI-T-002 — warm query response `[AUTO]`

- **Given:** the user has searched `cat` once already.
- **When:** the user searches `cat` again.
- **Then:** the API response time is < 50ms (LRU cache hit). Total page time < 200ms.

#### UI-T-003 — grid render time `[AUTO]`

- **Given:** 50 results.
- **When:** the page renders.
- **Then:** the result grid is visible within 100ms of the response completing.

### Group H — accessibility & usability (basic)

These are minimum-viable; the design is single-user, so we don't go deep into mobile/web deploy.

#### UI-X-001 — form is keyboard-accessible `[AUTO]`

- **Given:** the user is on `GET /`.
- **When:** the user presses Tab.
- **Then:** focus moves to the `q` input, then to the submit button. No traps.

#### UI-X-002 — submit via Enter `[AUTO]`

- **Given:** focus is in the `q` input.
- **When:** the user presses Enter.
- **Then:** the form submits. (Same as UI-S-003 but framed for keyboard-only users.)

#### UI-X-003 — `<img alt>` attributes are present `[AUTO]`

- **Given:** the result grid renders.
- **When:** the page is inspected.
- **Then:** every `<img>` has an `alt` attribute (empty string is acceptable for decorative thumbnails).

#### UI-X-004 — page is usable at 1280×800 `[MANUAL]`

- **Given:** a desktop browser at 1280×800.
- **When:** the user interacts with the page.
- **Then:** the result grid is visible without horizontal scroll. The search form is visible without scroll.

#### UI-X-005 — color contrast is reasonable `[MANUAL]`

- **Given:** the rendered page.
- **When:** inspected visually.
- **Then:** text and background contrast is readable. (No formal WCAG audit — single-user tool.)

## Multi-prompt search

#### UI-MP-001 — Include is the primary search mode `[AUTO]`

- **Given:** the user opens `GET /`.
- **When:** the user types `cat` into Include and clicks Search without adding another chip.
- **Then:** the URL has `?positives=cat`, the result grid renders, and no second query field is required.

#### UI-MP-002 — add a positive chip `[AUTO]`

- **Given:** the search page is open.
- **When:** the user types `kitten` in the include prompt row and presses Enter or clicks `+`.
- **Then:** an include chip labeled `kitten` appears with positive styling.

#### UI-MP-003 — add a negative chip `[AUTO]`

- **Given:** the search page is open.
- **When:** the user types `blurry` in the exclude prompt row and presses Enter or clicks `+`.
- **Then:** an exclude chip labeled `blurry` appears with distinct grey/italic styling.

#### UI-MP-004 — remove a chip `[AUTO]`

- **Given:** a prompt chip is visible.
- **When:** the user clicks the chip's `×`.
- **Then:** the chip is removed and is not included in the next submitted URL.

#### UI-MP-005 — submit mixed prompts `[AUTO]`

- **Given:** the user has Include chips `cat`, `kitten`, and `portrait`, and Exclude chip `blurry`.
- **When:** the user clicks Search.
- **Then:** the URL contains repeated `positives=` params for the Include chips and repeated `negatives=` params for Exclude chips.

#### UI-MP-006 — refresh hydrates chips `[AUTO]`

- **Given:** the browser is on `GET /?positives=cat&positives=kitten&negatives=blurry`.
- **When:** the user refreshes the page.
- **Then:** the include chips `cat` and `kitten` and the exclude chip `blurry` are visible.

#### UI-MP-007 — photo back link preserves prompts `[AUTO]`

- **Given:** the user opens a photo from a multi-prompt search.
- **When:** the user clicks `← Back to results`.
- **Then:** the browser returns to the multi-prompt search URL and the chips are intact.

#### UI-MP-008 — library filter waits for Search `[AUTO]`

- **Given:** the user has include/exclude chips set.
- **When:** the user toggles a library chip filter.
- **Then:** the chip changes visually, but the URL, result grid, and search request remain unchanged.
- **When:** the user clicks Search.
- **Then:** the committed URL adds or removes `collection=` while preserving the prompts.

#### UI-MP-009 — positives without negatives submit `[AUTO]`

- **Given:** the user has one or more include chips and no exclude chips.
- **When:** the user submits.
- **Then:** the search runs successfully.

#### UI-MP-010 — negatives-only attempt is rejected `[AUTO]`

- **Given:** the user has one or more exclude chips and no primary query or include chips.
- **When:** the user submits.
- **Then:** the form shows an inline error and does not navigate, or the server returns `400 bad_request` and the JS surfaces the error.

---

## View mode (grid / feed)

The result list has two presentation modes selected via a `?view=` URL param
and a segmented control in the search form row. The data is the same —
only the per-item layout differs (multi-column grid vs. single-column
phone-style feed with natural image aspect).

#### UI-VIEW-001 — toggle renders for every result page `[AUTO]`

- **Given:** a results page (server-rendered or post-hydration).
- **When:** the page loads.
- **Then:** the search form row contains a `grid / feed` segmented control with two buttons, both with `data-view` attributes.

#### UI-VIEW-002 — toggle reflects current view on load `[AUTO]`

- **Given:** the URL has `?view=feed`.
- **When:** the page loads.
- **Then:** the `feed` button is `aria-pressed="true"` and `.view-toggle-btn--active`; the `grid` button is `aria-pressed="false"`.

#### UI-VIEW-003 — toggle reflects current view on load (default) `[AUTO]`

- **Given:** the URL has no `?view=` (or `?view=grid`).
- **When:** the page loads.
- **Then:** the `grid` button is `aria-pressed="true"`; the `feed` button is `aria-pressed="false"`.

#### UI-VIEW-004 — click feed stages feed layout `[AUTO]`

- **Given:** a result page in grid mode with results visible.
- **When:** the user clicks the `feed` toggle.
- **Then:** the feed setting becomes active without changing the URL or fetching results. The next Search commits `?view=feed` and renders the feed.

#### UI-VIEW-005 — click grid stages grid layout `[AUTO]`

- **Given:** a result page in feed mode with results visible.
- **When:** the user clicks the `grid` toggle.
- **Then:** the grid setting becomes active without changing the URL or fetching results. The next Search omits `?view=` for the default.

#### UI-VIEW-006 — toggle click on the active view is a no-op `[AUTO]`

- **Given:** the user is in grid mode.
- **When:** the user clicks the `grid` toggle (the active one).
- **Then:** no URL change, no re-render, no network call.

#### UI-VIEW-007 — feed view preserves prompts and library filter `[AUTO]`

- **Given:** the user has `?positives=cat&positives=kitten&negatives=blurry&collection=foo` in the URL.
- **When:** the user clicks the `feed` toggle and then Search.
- **Then:** the URL becomes `?positives=cat&positives=kitten&negatives=blurry&collection=foo&view=feed`; the search runs with the same prompts/filter.

#### UI-VIEW-008 — back link from photo page restores the view `[AUTO]`

- **Given:** the user is on the photo detail page after viewing results in feed mode (URL has `view=feed`).
- **When:** the user clicks the "back to results" link.
- **Then:** they land on the search page with `?view=feed` in the URL and the feed toggle is active.

#### UI-VIEW-009 — feed view supports infinite scroll `[AUTO]`

- **Given:** the user is in feed mode with `has_more=true` (sentinel rendered).
- **When:** the user scrolls near the bottom of the feed.
- **Then:** the next page of results is appended below the current feed; the URL keeps `view=feed`; the feed layout is preserved.

#### UI-VIEW-010 — feed view supports score overlay on each item `[AUTO]`

- **Given:** the user is in feed mode with results.
- **When:** the page renders.
- **Then:** each `.feed-item` has a `.feed-score` element with the formatted cosine score (3 decimals, e.g. `0.873`), positioned in the top-left of the image.

#### UI-VIEW-011 — feed layout is responsive `[AUTO]`

- **Given:** the viewport is wider than 760px.
- **When:** the feed view is rendered.
- **Then:** the feed container is centered with a `max-width: 720px`.
- **Given:** the viewport is 760px or narrower.
- **Then:** the feed container goes full-bleed (no max-width constraint) and the site-main padding provides the gutter.

#### UI-VIEW-012 — invalid view value falls back to grid `[AUTO]`

- **Given:** the URL has `?view=garbage` (or any non-`grid`/`feed` value).
- **When:** the page loads.
- **Then:** the result list renders in grid mode; the response JSON's `view` field is `"grid"`; the toggle's `grid` button is `aria-pressed="true"`.
