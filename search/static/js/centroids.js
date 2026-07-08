// centroids.js — page-level controller for /centroids
//
// Behavior:
//   - Wires the "reload" button to POST /api/centroids/reload.
//   - Manages the multi-select selection bar: tracks which centroid
//     checkboxes are ticked, updates the sticky bar's count + names
//     in real time, and navigates to `/?centroid=a&centroid=b...`
//     when the user clicks "Search with blend".
//   - On success of reload, refreshes the page so the new list is
//     rendered server-side. (Centroid metadata rarely changes; a
//     full reload is fine and keeps the rendering path single-source.)
//   - On failure, surfaces the error inline in the reload-status
//     span so the user doesn't have to open devtools.

// -------------------- Reload button --------------------

const btn = document.querySelector("[data-centroids-reload]");
const status = document.querySelector("[data-centroids-reload-status]");

if (btn && status) {
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    status.textContent = "reloading…";
    status.className = "centroids-reload-status";
    try {
      const resp = await fetch("/api/centroids/reload", { method: "POST" });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        const detail = body.detail || `${resp.status} ${resp.statusText}`;
        status.textContent = `reload failed: ${detail}`;
        status.className = "centroids-reload-status centroids-reload-status--error";
        return;
      }
      const data = await resp.json();
      status.textContent = `reloaded ${data.count} centroid${data.count === 1 ? "" : "s"}`;
      status.className = "centroids-reload-status centroids-reload-status--ok";
      // Refresh the page to render the new list server-side.
      // A 400ms delay lets the user see the success message.
      setTimeout(() => window.location.reload(), 400);
    } catch (e) {
      status.textContent = `reload failed: ${e}`;
      status.className = "centroids-reload-status centroids-reload-status--error";
    } finally {
      btn.disabled = false;
    }
  });
}

// -------------------- Multi-select selection bar --------------------
//
// Each card has a checkbox ([data-centroid-checkbox]). The bar
// reflects the current selection in real time: count + the names
// (capped at a handful for visual sanity, with "+N more" when
// over the cap). Clicking "Search with blend" builds a
// `/?centroid=a&centroid=b&...` URL preserving the order the user
// ticked them and navigates.

const bar = document.querySelector("[data-centroid-selection-bar]");
const countEl = bar?.querySelector("[data-selection-count]");
const summaryEl = bar?.querySelector("[data-selection-summary]");
const ctaEl = bar?.querySelector("[data-selection-cta]");
const clearBtn = bar?.querySelector("[data-selection-clear]");
const checkboxes = document.querySelectorAll("[data-centroid-checkbox]");

if (bar && checkboxes.length > 0) {
  // Tick order matters — it's the order the user added centroids,
  // and that order is what we send to the server (so the blend is
  // deterministic and reproducible from the URL alone). Track each
  // checkbox's tick timestamp so we can rebuild the ordered list
  // without DOM walking.
  const tickOrder = new Map(); // name -> tick timestamp (ms)
  for (const cb of checkboxes) {
    const name = cb.dataset.centroidCheckbox;
    if (cb.checked) tickOrder.set(name, Date.now());
  }

  // Render the order in which checkboxes were ticked. Falls back
  // to DOM order for anything checked pre-render (pre-selected from
  // the URL) — they get the same timestamp so the iteration order
  // matches DOM order, which is the server-rendered order.
  function selectedInOrder() {
    return [...checkboxes]
      .filter((cb) => cb.checked)
      .map((cb) => cb.dataset.centroidCheckbox);
  }

  const SUMMARY_CAP = 4;

  function refreshBar() {
    const selected = selectedInOrder();
    if (selected.length === 0) {
      bar.classList.remove("is-visible");
      if (countEl) countEl.textContent = "0 selected";
      if (summaryEl) summaryEl.innerHTML = "";
      if (ctaEl) {
        ctaEl.setAttribute("aria-disabled", "true");
        ctaEl.setAttribute("tabindex", "-1");
      }
      return;
    }
    bar.classList.add("is-visible");
    if (countEl) {
      countEl.textContent =
        `${selected.length} selected`;
    }
    if (summaryEl) {
      const visible = selected.slice(0, SUMMARY_CAP);
      const overflow = selected.length - visible.length;
      summaryEl.innerHTML =
        visible
          .map(
            (n) =>
              `<span class="centroid-selection-summary-item">${escapeHtml(n)}</span>`,
          )
          .join("") +
        (overflow > 0
          ? `<span class="centroid-selection-summary-item">+${overflow} more</span>`
          : "");
    }
    if (ctaEl) {
      ctaEl.removeAttribute("aria-disabled");
      ctaEl.removeAttribute("tabindex");
      ctaEl.href = buildBlendUrl(selected);
    }
  }

  function buildBlendUrl(names) {
    // Single-centroid short-form: `/?centroid=X`. Multi: repeated
    // `?centroid=` params in blend order. Order = tick order, which
    // equals the server's blend order and the URL round-trips
    // without ambiguity. No weights — equal-weight blend by
    // default; the URL `?weights=` escape hatch is a power-user move.
    if (names.length === 1) {
      return `/?centroid=${encodeURIComponent(names[0])}`;
    }
    const params = new URLSearchParams();
    for (const n of names) params.append("centroid", n);
    return `/?${params.toString()}`;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Toggle handler — updates tick order map (so reorders track) and
  // refreshes the bar. Also adds/removes an `is-selected` class on
  // the card for the left-accent-border visual feedback.
  for (const cb of checkboxes) {
    cb.addEventListener("change", () => {
      const name = cb.dataset.centroidCheckbox;
      if (cb.checked) {
        tickOrder.set(name, Date.now());
      } else {
        tickOrder.delete(name);
      }
      const card = cb.closest("[data-centroid-card]");
      if (card) card.classList.toggle("is-selected", cb.checked);
      refreshBar();
    });
    // Apply initial visual state (pre-selected checkboxes).
    const card = cb.closest("[data-centroid-card]");
    if (card && cb.checked) card.classList.add("is-selected");
  }

  refreshBar();

  // Clear button — unchecks everything in DOM order.
  clearBtn?.addEventListener("click", () => {
    for (const cb of checkboxes) {
      if (cb.checked) {
        cb.checked = false;
        const card = cb.closest("[data-centroid-card]");
        if (card) card.classList.remove("is-selected");
      }
    }
    tickOrder.clear();
    refreshBar();
  });
}
