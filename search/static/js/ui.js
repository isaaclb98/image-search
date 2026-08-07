// Global UI layer: blurhash hydration, lightbox interactions, quick
// favourites, and the small keyboard shortcut surface shared by every page.

import { enhancePhotoCards, setFavoriteLabel } from "./lib/photo-card.js";

const lightbox = document.querySelector("[data-photo-lightbox]");
const lightboxImage = lightbox?.querySelector("[data-lightbox-image]");
const lightboxTitle = lightbox?.querySelector("[data-lightbox-title]");
const lightboxPath = lightbox?.querySelector("[data-lightbox-path]");
const lightboxDetail = lightbox?.querySelector("[data-lightbox-detail]");
const lightboxFavorite = lightbox?.querySelector("[data-lightbox-favorite]");
const lightboxAlbums = lightbox?.querySelector("[data-lightbox-albums]");
const lightboxAlbumList = lightbox?.querySelector("[data-lightbox-album-list]");
const lightboxAlbumStatus = lightbox?.querySelector("[data-lightbox-album-status]");
const shortcuts = document.querySelector("[data-shortcuts-dialog]");

let currentTrigger = null;
let lastFocused = null;
let albumList = null;
let albumRequestToken = 0;
const albumMemberships = new Map();

function allTriggers() {
  return Array.from(document.querySelectorAll("[data-lightbox-trigger]"));
}

function photoName(path, id) {
  const value = String(path || "").replace(/\\/g, "/").split("/").pop();
  return value || (id ? `Photo ${String(id).slice(0, 8)}…` : "Photo");
}

function updateLightbox(trigger) {
  if (!trigger || !lightboxImage) return;
  const id = trigger.dataset.photoId || "";
  const path = trigger.dataset.photoPath || "";
  lightboxImage.src = trigger.dataset.photoSrc || "";
  lightboxImage.alt = photoName(path, id);
  if (lightboxTitle) lightboxTitle.textContent = photoName(path, id);
  if (lightboxPath) lightboxPath.textContent = path || "Indexed photo";
  if (lightboxDetail) lightboxDetail.href = trigger.href || `/photo/${id}`;
  if (lightboxFavorite) {
    lightboxFavorite.dataset.favoriteId = id;
    const on = trigger.closest("li[data-photo-id]")?.querySelector("[data-favorite-toggle]")?.getAttribute("aria-pressed") === "true";
    setFavoriteLabel(lightboxFavorite, on);
    const label = lightboxFavorite.querySelector("span");
    if (label) label.textContent = on ? "Favourited" : "Favourite";
  }
  updateLightboxNavigation();
  void updateLightboxAlbums(id);
}

async function updateLightboxAlbums(photoId) {
  if (!lightboxAlbums || !lightboxAlbumList || !photoId) return;
  const requestToken = ++albumRequestToken;
  lightboxAlbums.hidden = false;
  lightboxAlbumList.innerHTML = "";
  if (lightboxAlbumStatus) lightboxAlbumStatus.textContent = "Loading…";
  try {
    if (albumList === null) {
      const response = await fetch("/api/albums", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`album list failed: ${response.status}`);
      const data = await response.json();
      albumList = Array.isArray(data?.albums) ? data.albums : [];
    }
    if (!albumMemberships.has(photoId)) {
      const response = await fetch(`/api/albums/by-favorite/${encodeURIComponent(photoId)}`, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`album membership failed: ${response.status}`);
      const data = await response.json();
      albumMemberships.set(photoId, new Set((data?.albums || []).map((album) => String(album.id))));
    }
    if (requestToken !== albumRequestToken || currentTrigger?.dataset.photoId !== photoId) return;
    const memberships = albumMemberships.get(photoId);
    if (!albumList.length) {
      lightboxAlbumList.innerHTML = '<p class="photo-lightbox-albums__empty">No albums yet. <a href="/albums">Create one</a> from the albums page.</p>';
      if (lightboxAlbumStatus) lightboxAlbumStatus.textContent = "";
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const album of albumList) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "photo-lightbox-album";
      button.dataset.lightboxAlbumToggle = "";
      button.dataset.albumId = String(album.id);
      button.dataset.photoId = photoId;
      const active = memberships.has(String(album.id));
      button.setAttribute("aria-pressed", active ? "true" : "false");
      button.classList.toggle("is-active", active);
      button.textContent = album.name || `Album ${album.id}`;
      fragment.appendChild(button);
    }
    lightboxAlbumList.appendChild(fragment);
    if (lightboxAlbumStatus) lightboxAlbumStatus.textContent = "";
  } catch (error) {
    if (requestToken !== albumRequestToken) return;
    console.debug("album actions unavailable", error);
    lightboxAlbumList.innerHTML = '<p class="photo-lightbox-albums__empty">Album actions are unavailable right now.</p>';
    if (lightboxAlbumStatus) lightboxAlbumStatus.textContent = "";
  }
}

async function toggleAlbum(button) {
  const albumId = button?.dataset.albumId;
  const photoId = button?.dataset.photoId;
  if (!albumId || !photoId || button.disabled) return;
  const wasOn = button.getAttribute("aria-pressed") === "true";
  const nextOn = !wasOn;
  button.disabled = true;
  button.setAttribute("aria-pressed", nextOn ? "true" : "false");
  button.classList.toggle("is-active", nextOn);
  try {
    const response = await fetch(`/api/albums/${encodeURIComponent(albumId)}/members/${encodeURIComponent(photoId)}`, {
      method: nextOn ? "POST" : "DELETE",
      headers: { Accept: "application/json" },
    });
    if (!response.ok && response.status !== 204) throw new Error(`album toggle failed: ${response.status}`);
    const memberships = albumMemberships.get(photoId) || new Set();
    if (nextOn) memberships.add(String(albumId));
    else memberships.delete(String(albumId));
    albumMemberships.set(photoId, memberships);
  } catch (error) {
    console.error(error);
    button.setAttribute("aria-pressed", wasOn ? "true" : "false");
    button.classList.toggle("is-active", wasOn);
  } finally {
    button.disabled = false;
  }
}

function updateLightboxNavigation() {
  const triggers = allTriggers();
  const index = Math.max(0, triggers.indexOf(currentTrigger));
  const prev = lightbox?.querySelector("[data-lightbox-prev]");
  const next = lightbox?.querySelector("[data-lightbox-next]");
  const multiple = triggers.length > 1;
  if (prev) {
    prev.disabled = !multiple;
    prev.hidden = !multiple;
    prev.dataset.index = String(index);
  }
  if (next) {
    next.disabled = !multiple;
    next.hidden = !multiple;
    next.dataset.index = String(index);
  }
}

function openLightbox(trigger) {
  if (!lightbox || !trigger) return;
  currentTrigger = trigger;
  lastFocused = document.activeElement;
  updateLightbox(trigger);
  lightbox.hidden = false;
  lightbox.classList.add("is-open");
  document.body.classList.add("lightbox-open");
  lightbox.querySelector("[data-lightbox-close]")?.focus();
}

function closeLightbox() {
  if (!lightbox || lightbox.hidden) return;
  lightbox.hidden = true;
  lightbox.classList.remove("is-open");
  document.body.classList.remove("lightbox-open");
  lightboxImage?.removeAttribute("src");
  const focusTarget = currentTrigger || lastFocused;
  currentTrigger = null;
  if (focusTarget && typeof focusTarget.focus === "function" && document.contains(focusTarget)) {
    focusTarget.focus();
  }
}

function moveLightbox(delta) {
  const triggers = allTriggers();
  if (!triggers.length) return;
  const current = Math.max(0, triggers.indexOf(currentTrigger));
  const next = triggers[(current + delta + triggers.length) % triggers.length];
  if (next) {
    currentTrigger = next;
    updateLightbox(next);
  }
}

function syncFavoriteButtons(id, on) {
  for (const button of document.querySelectorAll(`[data-favorite-toggle][data-favorite-id="${CSS.escape(id)}"]`)) {
    setFavoriteLabel(button, on);
    const label = button.querySelector("span");
    if (label) label.textContent = on ? (button === lightboxFavorite ? "Favourited" : "") : (button === lightboxFavorite ? "Favourite" : "");
  }
  for (const card of document.querySelectorAll(`li[data-photo-id="${CSS.escape(id)}"]`)) {
    card.classList.toggle("is-favourite", on);
    const badge = card.querySelector(".fav-badge");
    if (on && !badge) {
      const nextBadge = document.createElement("span");
      nextBadge.className = "fav-badge";
      nextBadge.setAttribute("aria-label", "Favourite");
      nextBadge.textContent = "♥";
      card.prepend(nextBadge);
    } else if (!on) {
      badge?.remove();
    }
  }
}

function removeFavoriteCard(id) {
  if (window.location.pathname !== "/favorites") return;
  const card = document.querySelector(`li[data-photo-id="${CSS.escape(id)}"]`);
  if (!card) return;
  const wasCurrent = currentTrigger && card.contains(currentTrigger);
  const oldIndex = wasCurrent ? allTriggers().indexOf(currentTrigger) : -1;
  card.remove();
  if (!wasCurrent || !lightbox || lightbox.hidden) return;
  const triggers = allTriggers();
  if (!triggers.length) {
    closeLightbox();
    return;
  }
  currentTrigger = triggers[Math.min(Math.max(oldIndex, 0), triggers.length - 1)];
  updateLightbox(currentTrigger);
}

async function toggleFavorite(button) {
  const id = button?.dataset.favoriteId;
  if (!id || button.disabled) return;
  const wasOn = button.getAttribute("aria-pressed") === "true";
  const nextOn = !wasOn;
  button.disabled = true;
  syncFavoriteButtons(id, nextOn);
  try {
    const response = await fetch(`/api/favorites/${encodeURIComponent(id)}`, {
      method: nextOn ? "POST" : "DELETE",
      headers: { Accept: "application/json" },
    });
    if (!response.ok && response.status !== 204) throw new Error(`favorite toggle failed: ${response.status}`);
    if (!nextOn) {
      removeFavoriteCard(id);
      window.dispatchEvent(new CustomEvent("favoritechanged", { detail: { id, on: false } }));
    }
  } catch (error) {
    console.error(error);
    syncFavoriteButtons(id, wasOn);
  } finally {
    for (const peer of document.querySelectorAll(`[data-favorite-toggle][data-favorite-id="${CSS.escape(id)}"]`)) peer.disabled = false;
  }
}

function setDialogOpen(element, open) {
  if (!element) return;
  element.hidden = !open;
  element.classList.toggle("is-open", open);
  if (open) document.body.classList.add("dialog-open");
  else if (lightbox?.hidden !== false) document.body.classList.remove("dialog-open");
}

function openShortcuts() {
  if (!shortcuts) return;
  lastFocused = document.activeElement;
  setDialogOpen(shortcuts, true);
  shortcuts.querySelector("[data-shortcuts-close]")?.focus();
}

function closeShortcuts() {
  if (!shortcuts || shortcuts.hidden) return;
  setDialogOpen(shortcuts, false);
  if (lastFocused && document.contains(lastFocused)) lastFocused.focus();
}

function trapFocus(container, event) {
  if (event.key !== "Tab") return;
  const focusable = Array.from(container.querySelectorAll(
    "button:not([disabled]):not([hidden]), a[href], input:not([disabled]), [tabindex]:not([tabindex='-1'])",
  ));
  if (focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

document.addEventListener("click", (event) => {
  const trigger = event.target.closest?.("[data-lightbox-trigger]");
  if (trigger && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) {
    event.preventDefault();
    openLightbox(trigger);
    return;
  }
  const favorite = event.target.closest?.("[data-favorite-toggle]");
  if (favorite) {
    event.preventDefault();
    event.stopPropagation();
    toggleFavorite(favorite);
    return;
  }
  const album = event.target.closest?.("[data-lightbox-album-toggle]");
  if (album) {
    event.preventDefault();
    event.stopPropagation();
    void toggleAlbum(album);
    return;
  }
  if (event.target.closest?.("[data-lightbox-prev]")) {
    event.preventDefault();
    moveLightbox(-1);
    return;
  }
  if (event.target.closest?.("[data-lightbox-next]")) {
    event.preventDefault();
    moveLightbox(1);
    return;
  }
  if (event.target.closest?.("[data-lightbox-close]")) closeLightbox();
  if (event.target.closest?.("[data-shortcuts-close]")) closeShortcuts();
  if (event.target.closest?.("[data-keyboard-help]")) openShortcuts();
});

document.addEventListener("keydown", (event) => {
  const editable = event.target.matches?.("input, textarea, select, [contenteditable='true']");
  if (event.key === "Escape") {
    if (lightbox && !lightbox.hidden) closeLightbox();
    else if (shortcuts && !shortcuts.hidden) closeShortcuts();
    return;
  }
  if (lightbox && !lightbox.hidden) {
    trapFocus(lightbox, event);
    if (event.key === "ArrowLeft") { event.preventDefault(); moveLightbox(-1); }
    if (event.key === "ArrowRight") { event.preventDefault(); moveLightbox(1); }
    return;
  }
  if (shortcuts && !shortcuts.hidden) {
    trapFocus(shortcuts, event);
    return;
  }
  if (editable) return;
  if (event.key === "/") {
    const includePrompt = document.querySelector('[data-prompt-input="positives"]');
    if (includePrompt) { event.preventDefault(); includePrompt.focus(); includePrompt.select(); }
  } else if (event.key === "?") {
    event.preventDefault();
    openShortcuts();
  }
});

enhancePhotoCards(document);
if ("MutationObserver" in window) {
  const observer = new MutationObserver(() => enhancePhotoCards(document));
  observer.observe(document.body, { childList: true, subtree: true });
}

window.imageSearchUI = { enhancePhotoCards, openLightbox, closeLightbox };
