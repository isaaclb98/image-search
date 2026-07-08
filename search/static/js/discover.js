// discover.js — discovery rabbithole controller
//
// Behavior contract:
//   - On page load, POST /api/discover/start, render the first pair.
//   - Clicking an image: POST /api/discover/pick with the picked id,
//     then swap in the next pair from the response.
//   - "done →" link takes you to /discover/liked?session_id=...
//   - If a pick returns pair=null (session gone), redirect to /discover
//     so the user can start a new session.

const root = document.querySelector("[data-discover-root]");
const pairEl = document.querySelector("[data-discover-pair]");
const roundEl = document.querySelector("[data-discover-round]");
const likedCountEl = document.querySelector("[data-discover-liked-count]");
const doneLink = document.querySelector("[data-discover-done]");
const loadingEl = document.querySelector("[data-discover-loading]");

let sessionId = null;
let picking = false;

async function start() {
  if (!pairEl) return;
  try {
    const resp = await fetch("/api/discover/start", {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) {
      renderError("Could not start a discovery session.");
      return;
    }
    const data = await resp.json();
    sessionId = data.session_id;
    renderPair(data.pair);
    updateRound(1, 0);
  } catch (err) {
    console.error("discover start failed", err);
    renderError("Could not start a discovery session.");
  }
}

function renderPair(pair) {
  if (!pairEl) return;
  pairEl.innerHTML = "";
  if (!pair || (!pair.left && !pair.right)) {
    // The collection is exhausted for this session. Nothing to
    // show. Tell the user.
    pairEl.innerHTML = `<p class="state state-warn">No more images in this discovery session. <a href="/discover/liked?session_id=${encodeURIComponent(sessionId || "")}">see your picks →</a></p>`;
    return;
  }
  const grid = document.createElement("div");
  grid.className = "discover-pair-grid";
  if (pair.left) grid.appendChild(renderImageButton(pair.left, "left"));
  if (pair.right) grid.appendChild(renderImageButton(pair.right, "right"));
  pairEl.appendChild(grid);
}

function renderImageButton(img, side) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "discover-image-btn";
  btn.dataset.imageId = img.id;
  btn.dataset.side = side;
  btn.setAttribute("aria-label", `Pick the ${side} image`);

  const el = document.createElement("img");
  el.src = img.url;
  el.alt = "";
  el.loading = "eager"; // current pair should be instant
  el.decoding = "async";
  el.addEventListener("error", () => el.classList.add("img-error"));
  btn.appendChild(el);

  btn.addEventListener("click", () => onPick(img.id, btn));
  return btn;
}

async function onPick(imageId, btn) {
  if (picking || !sessionId) return;
  picking = true;
  // Brief visual feedback: fade the picked one, dim the other.
  const allBtns = pairEl.querySelectorAll(".discover-image-btn");
  for (const b of allBtns) {
    b.disabled = true;
    if (b === btn) b.classList.add("discover-image-btn--picked");
    else b.classList.add("discover-image-btn--dimmed");
  }
  try {
    const url = `/api/discover/pick?session_id=${encodeURIComponent(sessionId)}&image_id=${encodeURIComponent(imageId)}`;
    const resp = await fetch(url, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) {
      renderError("Could not record your pick.");
      return;
    }
    const data = await resp.json();
    if (data.pair === null) {
      // Session gone (TTL or server restart). Start a new one.
      window.location.href = "/discover";
      return;
    }
    updateRound(data.round + 1, data.liked_count);
    renderPair(data.pair);
  } catch (err) {
    console.error("discover pick failed", err);
    renderError("Could not record your pick.");
  } finally {
    picking = false;
  }
}

function updateRound(round, likedCount) {
  if (roundEl) roundEl.textContent = `round ${round}`;
  if (likedCountEl) {
    likedCountEl.textContent = `${likedCount} picked`;
  }
}

function renderError(message) {
  if (!pairEl) return;
  pairEl.innerHTML = `<p class="state state-error">${escapeHtml(message)} <a href="/discover">retry</a></p>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// "done →" link → gallery, with the current session id. If the
// session is gone, the gallery page handles that gracefully.
if (doneLink) {
  doneLink.addEventListener("click", (e) => {
    e.preventDefault();
    if (!sessionId) {
      window.location.href = "/discover";
      return;
    }
    window.location.href = `/discover/liked?session_id=${encodeURIComponent(sessionId)}`;
  });
}

start();

// ---------------- Gallery page (discover_liked.html) ----------------
// If we're on the gallery page, wire up the "copy paths" button.
const copyBtn = document.querySelector("[data-discover-copy-paths]");
if (copyBtn) {
  copyBtn.addEventListener("click", async () => {
    const paths = Array.from(
      document.querySelectorAll("#discover-liked-grid .grid-item")
    ).map((li) => li.querySelector("img")?.getAttribute("src") || "");
    // Actually the user wants filesystem paths, not URLs. Pull
    // them from the data attributes on the server-rendered HTML
    // (see template). For v1 we copy the rendered src URLs (which
    // include the id); a future v1.1 will surface the real path
    // from a payload field.
    const text = paths.join("\n");
    try {
      await navigator.clipboard.writeText(text);
      copyBtn.textContent = "copied!";
      setTimeout(() => (copyBtn.textContent = "copy paths to clipboard"), 1500);
    } catch (err) {
      // Fallback: select-and-copy via a textarea.
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        copyBtn.textContent = "copied!";
        setTimeout(() => (copyBtn.textContent = "copy paths to clipboard"), 1500);
      } catch (e2) {
        copyBtn.textContent = "copy failed";
      }
      document.body.removeChild(ta);
    }
  });
}
