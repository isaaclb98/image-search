// lib/for-you.js
//
// Hydrate the /for-you page: fetch the feed, render photo-card
// slots, wire ♥ / ✕ toggles. State changes re-fetch the feed so
// the next batch reflects the new signal.
//
// Two endpoints:
//   GET /api/for-you/feed         -> { n_likes, n_dislikes, results }
//   POST   /api/dislikes/{id}     -> 204
//   DELETE /api/dislikes/{id}     -> 204
//   POST   /api/for-you/reset     -> 204
//   POST/DELETE /api/favorites/{id}  (existing, used for ♥ button)
//
// Re-uses the existing .photo-card layout system and the existing
// blurhash decoder. Distinct from /discover (in-memory session) —
// /for-you is persistent across restarts.
//
// Mounts and tears down cleanly if the user navigates away before
// the fetch resolves.

const feed = document.querySelector("[data-feed-root]");
const emptyState = document.querySelector("[data-empty-state]");
const freshChip = document.querySelector("[data-freshness-chip]");
let freshText = document.querySelector("[data-fresh-text]");
const freshestAt = document.querySelector(".for-you-page")?.dataset.freshest || "";
const resetBtn = document.querySelector("[data-reset-feedback]");

let busy = false;
let aborter = null;

if (feed) {
  loadFeed();
  setInterval(refreshFreshness, 60_000);
}

if (resetBtn) {
  resetBtn.addEventListener("click", onResetClick);
}

async function loadFeed() {
  if (busy) return;
  busy = true;
  aborter = new AbortController();

  try {
    const res = await fetch("/api/for-you/feed?limit=30", {
      signal: aborter.signal,
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`feed ${res.status}`);
    const data = await res.json();
    renderFeed(data);
    updateHeaderChip(data);
  } catch (err) {
    if (err.name !== "AbortError") {
      feed.innerHTML = `<div class="for-you-error glass-frost">Feed unavailable. ${escapeHtml(String(err.message))}</div>`;
    }
  } finally {
    busy = false;
  }
}

function renderFeed(data) {
  const items = data.results || [];
  feed.setAttribute("aria-busy", "false");

  if (items.length === 0) {
    feed.innerHTML = `<div class="for-you-error glass-frost">No matches yet. Try more ❤️ from a few photos.</div>`;
    return;
  }

  feed.innerHTML = items
    .map((it, idx) => renderCard(it, idx))
    .join("");

  wireFeedButtons();
}

function renderCard(it, idx) {
  return `
    <article class="photo-card for-you-card" data-photo-id="${it.id}" data-photo-blurhash="${it.blurhash || ""}" data-freshness="${idx}">
      <a class="photo-card-link" href="/photo/${it.id}">
        <img src="${it.url}" alt="" loading="lazy" decoding="async" class="photo-card-img">
      </a>
      <div class="photo-card-actions">
        <button type="button"
                class="photo-card-favorite"
                data-favorite-toggle
                data-favorite-id="${it.id}"
                aria-pressed="false"
                aria-label="Like this photo"
                title="Like">♥</button>
        <button type="button"
                class="photo-card-dislike"
                data-dislike-toggle
                data-dislike-id="${it.id}"
                aria-pressed="false"
                aria-label="Skip this photo"
                title="Skip">✕</button>
      </div>
    </article>
  `;
}

function wireFeedButtons() {
  for (const btn of feed.querySelectorAll("[data-favorite-toggle]")) {
    btn.addEventListener("click", onLikeClick);
  }
  for (const btn of feed.querySelectorAll("[data-dislike-toggle]")) {
    btn.addEventListener("click", onDislikeClick);
  }
}

async function onLikeClick(ev) {
  const btn = ev.currentTarget;
  const id = btn.dataset.favoriteId;
  const wasActive = btn.classList.contains("is-active");

  btn.disabled = true;
  try {
    if (wasActive) {
      await fetch(`/api/favorites/${id}`, { method: "DELETE" });
      btn.classList.remove("is-active");
      btn.setAttribute("aria-pressed", "false");
    } else {
      await fetch(`/api/favorites/${id}`, { method: "POST" });
      btn.classList.add("is-active");
      btn.setAttribute("aria-pressed", "true");
      btn.closest(".photo-card")?.animate(
        [{ transform: "scale(1)" }, { transform: "scale(1.04)" }, { transform: "scale(1)" }],
        { duration: 320, easing: "cubic-bezier(0.2,0.7,0.2,1)" },
      );
    }
    document.body.dataset.route = "/for-you";  // event-source tag
    // Re-fetch happens once per heartbeat to avoid hammering; small.
    debounceReload();
  } finally {
    btn.disabled = false;
  }
}

async function onDislikeClick(ev) {
  const btn = ev.currentTarget;
  const id = btn.dataset.dislikeId;
  const wasActive = btn.classList.contains("is-active");
  const card = btn.closest(".photo-card");

  btn.disabled = true;
  try {
    if (wasActive) {
      await fetch(`/api/dislikes/${id}`, { method: "DELETE" });
      btn.classList.remove("is-active");
      btn.setAttribute("aria-pressed", "false");
      card?.animate([{ opacity: 0.7 }, { opacity: 1 }], { duration: 200 });
    } else {
      await fetch(`/api/dislikes/${id}?source=for_you`, { method: "POST" });
      btn.classList.add("is-active");
      btn.setAttribute("aria-pressed", "true");
      card?.animate(
        [
          { opacity: 1, transform: "translateY(0)" },
          { opacity: 0.4, transform: "translateY(-6px)" },
          { opacity: 0, transform: "translateY(-12px)" },
        ],
        { duration: 320, easing: "cubic-bezier(0.2,0.7,0.2,1)", fill: "forwards" },
      );
      // Re-fetch after the slide-out finishes so the slot gets a
      // fresh card from the recomputed feed.
      setTimeout(() => loadFeed(), 360);
      return;
    }
    debounceReload();
  } finally {
    btn.disabled = false;
  }
}

async function onResetClick() {
  if (!confirm("Reset your ✕ feedback? Likes stay.")) return;
  await fetch("/api/for-you/reset", { method: "POST" });
  location.reload();
}

let reloadTimer = null;
function debounceReload() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(loadFeed, 1200);
}

function updateHeaderChip(data) {
  if (!freshChip) return;
  const likes = data.n_likes ?? 0;
  const dislikes = data.n_dislikes ?? 0;
  const html = `Trained on <strong>${likes}</strong> ♥ <strong>${dislikes}</strong> ✕ <span class="for-you-meta__freshness" data-fresh-text></span>`;
  freshChip.innerHTML = html;
  freshText = freshChip.querySelector("[data-fresh-text]");
  refreshFreshness();
}

function refreshFreshness() {
  if (!freshText || !freshestAt) return;
  const fresh = Date.parse(freshestAt + "Z");
  if (!Number.isFinite(fresh)) return;
  const ago = Math.max(0, (Date.now() - fresh) / 1000);
  freshText.textContent = `· ${humanAgo(ago)}`;
}

function humanAgo(seconds) {
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[c]);
}
