# Image Search: Web vs Desktop Reconciliation Research

**Date:** 2025-01-26  
**Purpose:** Analyze differences between `image-search` (web) and `image-search-desktop` to identify what's complementary, what overlaps, and what should be scrapped. Web is the source of truth going forward.

---

## Architecture Comparison

### image-search (web)
- **Backend:** Python (FastAPI) + Qdrant + SQLite (index.db)
- **Inference:** SigLIP2 via open_clip/PyTorch (GPU-optimized, CPU fallback)
- **Frontend:** SvelteKit (static build, served by FastAPI)
- **Indexing:** CLI-based (`scripts/run-indexer.sh`), server-side
- **Storage:** Qdrant (vectors) + SQLite (metadata)
- **Deployment:** Docker (single container + Qdrant sidecar)
- **Users:** Multi-user ready (bcrypt auth, session cookies)
- **Features:** Full-featured (albums, favorites, dislikes, saved searches, centroids, discover, for-you, diversity)

### image-search-desktop
- **Backend:** Electron main process (TypeScript/Node.js)
- **Inference:** SigLIP2 via ONNX Runtime (CPU-optimized, ~888MB model)
- **Frontend:** SvelteKit (static build, loaded via Electron)
- **Indexing:** Background worker with progress tracking, local folder picker
- **Storage:** hnswlib-node (vectors, in-memory + disk persistence) + SQLite (metadata via better-sqlite3)
- **Deployment:** Electron app (NSIS installer for Windows, AppImage for Linux)
- **Users:** Single-user (local app, no auth)
- **Features:** Basic (text search, similar search, thumbnails)

---

## What Desktop Has That's Valuable

### 1. Model Download Manager with Progress UI
- **What:** `apps/main/src/inference/model-manager.ts` handles model downloads with progress tracking
- **Why valuable:** User-friendly first-run experience, resume support
- **Reconciliation:** Adopt pattern for web's lazy model loading (currently just logs progress)
- **Effort:** Low - adapt TypeScript logic to Python, add WebSocket/SSE for progress

### 3. Local Folder Picker + Background Indexing
- **What:** Native folder picker dialog, background worker with progress events
- **Why valuable:** Better UX than CLI-based indexing
- **Reconciliation:** Web already has `/api/system/reindex` endpoint; add folder picker UI + progress polling
- **Effort:** Low-Medium - frontend work, backend already supports it

### 4. Thumbnail Pipeline (sharp)
- **What:** Uses `sharp` (libvips) for fast thumbnail generation
- **Why valuable:** Web currently serves full-resolution images (perf issue noted in roadmap)
- **Reconciliation:** Adopt sharp-based pipeline for web (or Python equivalent like `pyvips`)
- **Effort:** Medium - need to build thumbnail generation + storage + serving

### 5. hnswlib for Single-User Scenarios
- **What:** In-memory vector index with disk persistence
- **Why valuable:** Simpler than Qdrant for local/personal deployments
- **Reconciliation:** **SKIP** - Qdrant is more robust, scalable, and already integrated. hnswlib is a dead end.
- **Effort:** N/A

---

## What Web Has That's Valuable (Desktop Lacks)

### 1. Full Feature Set
- Albums, favorites, dislikes, saved searches
- Centroids (visual clustering)
- Discover mode (exploration)
- For-you feed (recommendations)
- Diversity (result deduplication)
- **Reconciliation:** Desktop should adopt web's feature set (or be deprecated)

### 2. Advanced Search
- Multi-prompt search (positive + negative)
- Filename filtering
- Diversity strength control
- Centroid-based search
- **Reconciliation:** Desktop's search UI is basic; adopt web's `SearchComposer.svelte`

### 3. Multi-User Auth
- bcrypt password hashing
- Session cookies (itsdangerous)
- **Reconciliation:** Not applicable to desktop (single-user), but web's auth is production-ready

### 4. Streaming ZIP Downloads
- `/favorites/download.zip` and `/albums/{id}/download.zip`
- Uses `zipstream-ng` for memory-efficient streaming
- **Reconciliation:** Desktop doesn't need this (local files already accessible)

### 5. Better UI Polish
- Glass morphism aesthetic
- Responsive grid (virtual scrolling)
- Lightbox with keyboard navigation
- Context menus
- **Reconciliation:** Desktop's UI is basic; adopt web's components

---

## Shared Code Opportunities

### 1. Frontend Components (SvelteKit)
**Current state:** Both use SvelteKit, but desktop has basic UI, web has polished UI.

**Reconciliation:**
- Share `SearchComposer.svelte`, `SearchGrid.svelte`, `PhotoTile.svelte`, `Lightbox.svelte`
- Desktop becomes a thin Electron wrapper around web's frontend
- Or: deprecate desktop entirely, use web as PWA

**Effort:** Low - copy components, adapt IPC vs HTTP API calls

### 2. API Contract
**Current state:** 
- Web: OpenAPI schema (`/openapi.json`), HTTP REST
- Desktop: IPC channels (`packages/shared/src/index.ts`), no HTTP

**Reconciliation:**
- Desktop's IPC types could inform a shared API contract
- Or: desktop switches to HTTP (call web's FastAPI backend from Electron)
- Or: deprecate desktop

**Effort:** Medium if keeping desktop, N/A if deprecating

### 3. Model Loading
**Current state:**
- Web: Lazy loading in `text_encoder.py`, no progress UI
- Desktop: `model-manager.ts` with download progress

**Reconciliation:**
- Adopt desktop's model manager pattern for web
- Share model download logic (Python vs TypeScript, but same HuggingFace Hub API)

**Effort:** Low-Medium

---

## What Should Be Scrapped

### From Desktop
1. **Electron-specific code** (`apps/main/src/ipc/`, `preload.ts`)
   - Reason: If deprecating desktop, this is dead code
   - If keeping desktop: refactor to call web's HTTP API instead of duplicating logic

2. **hnswlib integration** (`apps/main/src/store/vector-index.ts`)
   - Reason: Qdrant is better (scalable, production-ready, already integrated in web)
   - hnswlib is fine for prototypes, but not worth maintaining

3. **Basic UI** (`apps/renderer/src/routes/+page.svelte`)
   - Reason: Web's UI is more polished and feature-complete
   - Desktop should use web's components or be deprecated

4. **Duplicate ADR docs** (`docs/adr/`)
   - Reason: Desktop copied ADRs from web, creating maintenance burden
   - Scrapped: desktop should link to web's docs, not duplicate

### From Web
- **Nothing major** - web is the source of truth
- Minor: CLI-based indexing could be enhanced with desktop's background worker pattern (but not scrapped)

---

## Reconciliation Strategies

### Strategy A: Desktop as Thin Wrapper (Recommended)
**Approach:**
1. Desktop's Electron app loads web's SvelteKit frontend (static build)
2. Desktop's main process calls web's FastAPI backend via HTTP (localhost)
3. Desktop adds: folder picker, model download UI, system tray integration
4. Deprecate desktop's backend logic (indexer, search, store)

**Pros:**
- Single source of truth (web)
- Desktop gets all web features for free
- Minimal code duplication

**Cons:**
- Desktop requires running web's Docker container (or Python backend)
- Heavier install (Docker + Electron)

**Effort:** Medium (2-3 weeks)

### Strategy B: Deprecate Desktop Entirely (Aggressive)
**Approach:**
1. Archive `image-search-desktop` repo
2. Focus all effort on web
3. Web becomes PWA (installable, offline-capable)
4. Add "local mode" to web: run FastAPI + Qdrant on localhost, no Docker

**Pros:**
- Zero code duplication
- Single product to maintain
- PWA is lighter than Electron

**Cons:**
- Loses native folder picker (browser limitation)
- Loses system tray integration
- Requires users to run Python/Qdrant locally

**Effort:** Low (1 week to archive + document)

### Strategy C: Shared Kernel + Separate Apps (Status Quo++)
**Approach:**
1. Extract shared code into `image-search-kernel` (TypeScript package)
2. Web uses kernel via PyO3 bindings (Python ↔ Rust ↔ TypeScript)
3. Desktop uses kernel directly (TypeScript)
4. Both apps share: inference, indexing, search logic

**Pros:**
- True code sharing
- Both apps can evolve independently

**Cons:**
- Massive effort (PyO3 bindings, kernel refactor)
- Maintains two products
- Over-engineered for current needs

**Effort:** High (2-3 months)

---

## Recommended Path Forward

### Short-term (1-2 weeks)
1. **Adopt desktop's model download manager for web**
   - Port `model-manager.ts` logic to Python
   - Add WebSocket/SSE for progress updates
   - Improve web's first-run UX

2. **Adopt desktop's thumbnail pipeline for web**
   - Use `pyvips` (Python binding for libvips, same as sharp)
   - Generate thumbnails at index time
   - Serve thumbnails instead of full-resolution images

3. **Document reconciliation strategy**
   - Decide: Strategy A (thin wrapper) or Strategy B (deprecate)
   - Update READMEs accordingly

### Medium-term (1-2 months)
**If Strategy A (thin wrapper):**
1. Refactor desktop to call web's HTTP API
2. Delete desktop's backend code (indexer, search, store)
3. Desktop becomes: Electron shell + folder picker + model download UI
4. Test desktop with web's Docker container

**If Strategy B (deprecate):**
1. Archive `image-search-desktop` repo
2. Add PWA support to web (manifest.json, service worker)
3. Document "local mode" setup (run web on localhost without Docker)
4. Migrate desktop users to web PWA

### Long-term (3-6 months)
- Focus on web's roadmap (EXIF, timeline, map view, etc.)
- If Strategy A: maintain desktop as thin wrapper, minimal changes
- If Strategy B: web is the only product, desktop is unsupported

---

## Open Questions

1. **Is desktop still actively used?** If no users, deprecate immediately (Strategy B).

2. **Do we need native folder picker?** Browsers can't pick folders (security), so if this is critical, keep desktop (Strategy A).

### 3. Is ONNX Runtime worth the effort? ~~NO~~ — Isaac confirmed ONNX was a failure in practice. PyTorch/open_clip is the path forward. Don't revisit.

4. **Should desktop use web's Qdrant, or keep hnswlib?** If Strategy A, use Qdrant (consistency). If Strategy B, irrelevant.

5. **Is the Electron install size acceptable?** Electron apps are ~150MB+. If this is a blocker, PWA is better (Strategy B).

---

## Conclusion

**Web is clearly the source of truth.** It has better features, better UI, and production-ready architecture. Desktop is a proof-of-concept with some valuable patterns (model download manager, thumbnail pipeline) that should be ported to web.

**Recommendation:** Adopt Strategy A (desktop as thin wrapper) if native folder picker is critical, or Strategy B (deprecate desktop) if PWA is acceptable. Either way, port desktop's model download manager and thumbnail pipeline to web first.

**Immediate action:** Start with model download manager and thumbnail pipeline (1-2 weeks), then decide on Strategy A vs B based on user feedback.
