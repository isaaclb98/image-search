// discover_liked.js — page controller for /discover/liked
//
// Behavior:
//   - Wires the grid/feed view toggle to navigate to the same page
//     with ?view=<grid|feed>. Server-renders the chosen view, so
//     no client-side re-render of the image list is needed.
//   - Wires the "copy paths to clipboard" button to copy the picked
//     image paths (one per line, in pick order) to the clipboard.
//     Uses the modern Clipboard API with a textarea fallback so it
//     works on http://localhost and over Tailscale without HTTPS.

const viewBtns = document.querySelectorAll(".segmented-btn");
for (const btn of viewBtns) {
  btn.addEventListener("click", () => {
    const nextView = btn.dataset.view;
    if (!nextView) return;
    // No-op if the user clicks the already-active view (matches the
    // search page behaviour — saves a navigation).
    if (btn.classList.contains("is-active")) return;
    const url = new URL(window.location.href);
    if (nextView === "grid") {
      // Grid is the default; drop the param to keep canonical URLs
      // clean (mirrors the search page's view-toggle handler).
      url.searchParams.delete("view");
    } else {
      url.searchParams.set("view", nextView);
    }
    window.location.href = url.pathname + (url.search || "");
  });
}

const copyBtn = document.querySelector("[data-discover-copy-paths]");
if (copyBtn) {
  copyBtn.addEventListener("click", async () => {
    // Collect paths from the rendered tiles. The <img> src is a URL
    // (e.g. http://localhost:8000/photo/<id>/raw) — we want the
    // underlying file path, which is on the data-path attribute
    // added by the discover backend on each grid/feed item. Falls
    // back to the photo URL if data-path is missing.
    const items = document.querySelectorAll(
      "#discover-liked-grid li[data-id]",
    );
    const paths = [];
    for (const li of items) {
      const path = li.dataset.path || `/photo/${li.dataset.id}`;
      paths.push(path);
    }
    const text = paths.join("\n");
    let copied = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        copied = true;
      }
    } catch (e) {
      // Clipboard API can reject on insecure contexts; fall through
      // to the textarea + execCommand path below.
    }
    if (!copied) {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        copied = document.execCommand("copy");
      } catch (e) {
        // ignore — surfaces in the status text below
      }
      document.body.removeChild(ta);
    }
    const original = copyBtn.textContent;
    copyBtn.textContent = copied
      ? `copied ${paths.length} path${paths.length === 1 ? "" : "s"}`
      : "copy failed — paths in console";
    copyBtn.disabled = true;
    if (!copied) console.warn("discover paths:", text);
    setTimeout(() => {
      copyBtn.textContent = original;
      copyBtn.disabled = false;
    }, 1800);
  });
}
