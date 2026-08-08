# Sentinel — Round 2 Verification

**Branch:** `feature/polished-ui-tasks` (tip `4939ad1`)
**Base:** `fd86f21`
**Fix commit under review:** `4939ad1` — `fix(ui): Sentinel round-1 findings`
**Round:** 2 of max 2
**Verdict:** **PASS** — fix commit resolves every round-1 finding; no new regressions
or new bugs introduced. Safe to merge.

---

## Protocol steps

| # | Step | Result |
|---|------|--------|
| 1 | Branch checkout + `git log fd86f21..feature/polished-ui-tasks` | OK — 6 commits (5 original T6/T7/T9/T10/T11/T12 + the round-1 fix) |
| 2 | `git diff --stat fd86f21..feature/polished-ui-tasks` | OK — 21 files, +1414/-128. New files (`indexer/blurhash.py`, `search/templates/_macros.html`, `tests/test_{blurhash,states,theme}.py`, `.github/workflows/dev-build.yml`) are the expected deliverables; everything else is in-scope polish |
| 3 | Critical finding — `--reblurhash` wiring | **PASS** — see "Critical finding" below |
| 4 | Major finding — stray `</header>` in random.html | **PASS** — see "Major finding" below |
| 5 | Minor findings — input.css polish + `_results_from_hits` cleanup | **PASS** — see "Minor findings" below |
| 6 | Read full `git diff 80e8993..4939ad1` and look for new bugs | OK — clean. See "New-bug audit" below |
| 7 | `pytest tests/ -q` | **PASS** — `483 passed, 2 warnings in 40.46s` (matches expected) |
| 8 | `python -m indexer.indexer --help \| grep -A 2 reblurhash` | **PASS** — flag appears with full help text |

### Additional checks performed
- Rebuilt the design-system bundle (`./bin/tailwindcss -i search/static/css/input.css -o search/static/css/app.css`) to confirm the polish overrides land in the shipped CSS. Working tree remained clean (`git status` empty) — the rebuilt bytes matched the committed blob `5dfda47` byte-for-byte.
- Jinja2 `env.parse()` of `random.html` and `centroids.html` — both `parse OK`.
- Stubbed-Qdrant end-to-end simulation of the `--reblurhash` branch with a real PNG to verify cursor pagination, idempotency, and `set_payload` payload shape.

---

## Critical finding — `--reblurhash` flag (round-1 critical)

| Check | Where | Result |
|-------|-------|--------|
| Docstring lists `--reblurhash` under Usage | `indexer/indexer.py:36-39` | ✅ present |
| `parse_args` declares it (`p.add_argument("--reblurhash", ...)`) | `indexer/indexer.py:128-134` | ✅ present, `action="store_true"` |
| `main()` has the early-return branch | `indexer/indexer.py:241-287` | ✅ present (after `--prune` early-return, before the normal scan path) |
| argparse accepts it (help output) | `.venv/bin/python -m indexer.indexer --help 2>&1 \| grep -B1 -A4 reblurhash` | ✅ flag appears with the help text: *"walk the existing collection, recompute blurhash for each point from its source file, and rewrite only the 'blurhash' payload field via set_payload. Does NOT re-embed. Idempotent: re-running on a current collection is a no-op. Mutually exclusive with the normal index path."* |
| argparse accepts it (dry run) | `.venv/bin/python -m indexer.indexer /tmp --reblurhash --qdrant-in-memory --collection test 2>&1 \| head -20` | ✅ no `unrecognized arguments` error; prints the two expected log lines: `reblurhash: walking collection ...` and `reblurhash complete: updated=0 skipped=0 failed=0` |
| `compute_blurhash` import path | `indexer/indexer.py:246` — `from indexer.blurhash import compute_blurhash as _compute_blurhash` | ✅ resolves correctly (smoke-imported: `<function compute_blurhash ...>`) |
| `client.scroll` arg shape | `collection_name=`, `limit=256`, `offset=` (None / cursor), `with_payload=True`, `with_vectors=False` | ✅ matches the qdrant-client signature; confirmed by stubbed walk |
| `client.set_payload` arg shape | `collection_name=`, `payload={"blurhash": new_hash}`, `points=[rec.id]` | ✅ matches; confirmed by stub: call captured as `('t', {'blurhash': 'L5Bh]8yZfQyZyZj]fQj]fQfQfQfQ'}, [1])` |
| Cursor pagination termination | Stub returns `(recs, 'cursor-1')` then `(recs, None)` | ✅ loop terminates on second iteration; `offset=next_offset` then `next_offset is None → break` |

### End-to-end behavioral verification (stubbed Qdrant + real PIL image)

| Rec | payload.path | payload.blurhash | Path file | Action | Result |
|-----|--------------|------------------|-----------|--------|--------|
| 1 | `/tmp/rb_fixture/x.png` | `"WRONG"` | real 64×64 PNG | compute → `"L5Bh]8yZfQy…"` ≠ existing | `set_payload` called with `{'blurhash': 'L5Bh]8yZfQy…'}, [1]` |
| 2 | `/tmp/rb_fixture/x.png` | `"L5Bh]8yZfQy…"` (matches) | real 64×64 PNG | compute → identical | skipped (no `set_payload` call) |

Final counters: `updated=1 skipped=1 failed=0`, `rc=0`. **Idempotency confirmed.**

**Verdict:** **RESOLVED.**

---

## Major finding — stray `</header>` in random.html (round-1 major)

`grep -c '</header>' search/templates/random.html` → `0`
`grep -c '<header' search/templates/random.html` → `0`

Both opening and closing `<header>` tags are removed from `random.html`. The diff
replaces the stray closing line with whitespace (`  `) — visually balanced in
absence, and `{{ ui.page_header("Random") }}` already supplies the heading through
the macro. Jinja `env.parse()` returns `parse OK`.

**Verdict:** **RESOLVED.**

---

## Minor findings

### Minor 1 — input.css "Component polish" overrides `.grid-item` / `.feed-item`

`search/static/css/input.css:143-172` adds a "7. Component polish" section after
the responsive media query:

```css
.grid-item,
.feed-item {
  border: 0;                          /* drop the site.css 1px glass-border */
  transition: box-shadow 150ms ease-out;
}
.grid-item:hover,
.feed-item:hover,
.grid-item:focus-within,
.feed-item:focus-within {
  box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1),
              0 4px 6px -4px rgba(0, 0, 0, 0.1);
}
.grid-item:hover,
.feed-item:hover {
  transform: none;                    /* cancel site.css translateY(-2px) */
}
```

Specificity: both the legacy site.css `.grid-item` and the new input.css override
have specificity (0,1,0). Cascade → later rule wins. Confirmed in the built
`app.css`:

| app.css line | rule |
|--------------|------|
| 50958 | `.grid-item, .feed-item { border: 0; transition: box-shadow 150ms ease-out; }` |
| 50962 | `.grid-item:hover, .feed-item:hover, .grid-item:focus-within, .feed-item:focus-within { box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -4px rgba(0,0,0,0.1); }` |
| 50965 | `.grid-item:hover, .feed-item:hover { transform: none; }` |

After rebuild (`./bin/tailwindcss -i search/static/css/input.css -o search/static/css/app.css`):
- `git status` clean → rebuilt bytes match committed blob `5dfda47`.
- Both override rules are present in the shipped CSS, in the order that
  guarantees they win over the legacy site.css at lines 306-396.

**Verdict:** **RESOLVED.** Border is dropped; translateY is cancelled; box-shadow hover lives in the shipped bundle.

### Minor 2 — `_results_from_hits` no longer uses `hasattr(h, "payload")`

`search/app.py:1153` (post-fix):

```python
blurhash=(h.payload or {}).get("blurhash"),
```

No `hasattr` guard, no conditional. Clean (and matches the same expression used
by the rest of the file). `h` is always a Qdrant `ScoredPoint` whose `.payload`
attribute exists by type, so the `hasattr` was redundant scaffolding.

**Verdict:** **RESOLVED.**

---

## New-bug audit — does `4939ad1` introduce regressions?

### A. `--reblurhash` runtime correctness

| Concern | Disposition |
|---------|-------------|
| Infinite loop in scroll pagination | Confirmed safe. Loop exits on `if not points: break` and on `if next_offset is None: break` after each page. Stub-walked 2 pages (cursor-1 → None) terminates in 2 `scroll()` invocations. No way for `next_offset` to be non-None forever: qdrant-client returns `None` when no more pages exist. |
| Off-by-one in scroll offset | `offset=next_offset` after handling each page; first iteration uses `offset=None` (start of collection). qdrant-client interprets `None` as "from the beginning". ✅ |
| `compute_blurhash` import path | `from indexer.blurhash import compute_blurhash as _compute_blurhash` (line 246) — function-local import, avoids forcing the optional `blurhash` package on every run. Smoke-imported successfully. The corresponding top-level consumer in `indexer/upsert.py:25` uses `from indexer.blurhash import compute_blurhash` and works fine — same module, same symbol. |
| `set_payload` payload dict shape | `payload={"blurhash": new_hash}` is the correct partial-payload update form (replaces only the `blurhash` field, leaves the rest of the payload untouched). Confirmed by stub: call was `('t', {'blurhash': 'L5Bh]8yZfQy…'}, [1])`. |
| Failure handling for missing/unreadable source files | `compute_blurhash` returns `None` for non-image / missing files → `failed += 1`. `payload.get("path")` falsy → `failed += 1`. Neither raises, neither wedges the loop. ✅ |
| Idempotency | `if payload.get("blurhash") == new_hash: skipped += 1; continue` → verified, rec 2 in the stub walk was skipped, no `set_payload` call. ✅ |

### B. CSS override correctness

| Concern | Disposition |
|---------|-------------|
| `border: 0` actually applies | Same specificity as legacy rule, later in source order → wins. Verified in built `app.css` at line 50958. ✅ |
| `translateY(-2px)` actually cancelled | `transform: none` at line 50965, later than legacy hover rules → wins. ✅ |
| Box-shadow hover applied | Lines 50962-50964 emit the expected shadow on `:hover` and `:focus-within` for both classes. ✅ |

### C. Template side-effects

| Concern | Disposition |
|---------|-------------|
| random.html syntax | `jinja2.Environment.parse()` → `parse OK`. The replacement (whitespace for the stray `</header>` line) introduces no unclosed tags. ✅ |
| centroids.html syntax | `jinja2.Environment.parse()` → `parse OK`. The single-`<div>`-inside-`<div class="alert alert-info">` is the canonical DaisyUI v5 alert structure (outer `.alert` for the colored background, inner `<div>` for the stacked title+body). No issue. ✅ |

### D. Test suite

`483 passed, 2 warnings in 40.46s`. The two warnings are the pre-existing
`StarletteDeprecationWarning` and `UserWarning: Payload indexes have no effect in the
local Qdrant` — both unchanged from round 1, neither from the fix commit.

**Verdict:** **NO NEW BUGS.**

---

## Scope guard — round-1 findings to NOT re-flag

Per the brief: "the existing no-op (centroids.html:158-169) should NOT be a
finding in round 2."

Confirmed: `search/templates/centroids.html:158-169` (current lines) is:

```html
  {% else %}
    <div class="alert alert-info max-w-2xl mx-auto my-8"><div>
      <strong>No centroids loaded.</strong>
      {% if not centroids_dir %}
        Set <code>CENTROIDS_DIR</code> to a directory of
        <code>.pt</code> files produced by
        <code>isaac-image-scoring</code> and restart the container.
      {% else %}
        The directory is empty or every file was skipped. Run
        <code>isaac-image-scoring extract</code> to generate a
        centroid, then click <em>reload</em> above.
      {% endif %}
    </div></div>
  {% endif %}
```

This is the canonical DaisyUI v5 `.alert` structure (outer wrapper styles the
tinted background, inner `<div>` provides a vertical stack for the strong title
and the body text). Round-1's "empty nested div" finding was stale. **Not
re-flagged.**

---

## Acceptance criteria (from round-1 brief)

| Criterion | Verdict |
|-----------|---------|
| argparse accepts `--reblurhash` | PASS |
| `--reblurhash` main() branch exists and works (no infinite loop, correct shape) | PASS |
| Docstring documents the flag | PASS |
| random.html has no stray `</header>` | PASS |
| input.css has "Component polish" overriding `.grid-item` / `.feed-item` border + hover | PASS |
| `_results_from_hits` no longer uses `hasattr(h, "payload")` | PASS |
| No new bugs introduced by the fix | PASS |
| Tests pass (≥483) | PASS — 483 |
| CSS bundle is current (no stale app.css) | PASS — rebuild matched committed blob byte-for-byte |
| centroids.html not re-flagged as empty-nested-div | N/A — stale finding explicitly excluded from round-2 scope |

**No critical / major / minor issues introduced or unresolved.**

---

## Final verdict: **PASS**

`feature/polished-ui-tasks` is cleared to merge into `main` by Isaac. The
Sentinel round-1 critical + major + minor findings are all resolved by commit
`4939ad1`, the reblurhash branch is correct end-to-end, the test suite is green,
and the design-system bundle is current. No further spawning.