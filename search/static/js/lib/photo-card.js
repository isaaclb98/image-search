// Shared photo-card behavior for server-rendered and dynamically appended tiles.

import { decodeBlurHash } from "./blurhash.js";

export function enhancePhotoCards(root = document) {
  if (!root?.querySelectorAll) return;
  for (const link of root.querySelectorAll("[data-lightbox-trigger]")) {
    if (link.dataset.photoCardReady === "true") continue;
    link.dataset.photoCardReady = "true";
    const img = link.querySelector("img");
    if (!img) continue;

    if (link.dataset.blurhash) {
      let canvas = link.querySelector("canvas.blurhash-canvas");
      if (!canvas) {
        canvas = document.createElement("canvas");
        canvas.className = "blurhash-canvas";
        canvas.setAttribute("aria-hidden", "true");
        link.prepend(canvas);
      }
      try {
        const width = 32;
        const height = 32;
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("Canvas unavailable");
        const imageData = context.createImageData(width, height);
        imageData.data.set(decodeBlurHash(link.dataset.blurhash, width, height, 1));
        context.putImageData(imageData, 0, 0);
        link.classList.add("has-blurhash");
      } catch (error) {
        // A malformed or old payload should never prevent the real image
        // from loading. The neutral surface remains a valid fallback.
        console.debug("blurhash decode skipped", error);
      }
    }

    const reveal = () => link.classList.add("is-loaded");
    img.addEventListener("load", reveal, { once: true });
    if (img.complete && img.naturalWidth > 0) reveal();
  }
}
export function addFavoriteButton(li, result) {
  const actions = document.createElement("div");
  actions.className = "photo-card-actions";
  const button = document.createElement("button");
  button.type = "button";
  button.className = `photo-card-favorite${result.is_favorite ? " is-active" : ""}`;
  button.dataset.favoriteToggle = "";
  button.dataset.favoriteId = result.id;
  button.setAttribute("aria-pressed", result.is_favorite ? "true" : "false");
  setFavoriteLabel(button, result.is_favorite);
  button.innerHTML = '<svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.8 8.7c0 5.3-8.8 10.1-8.8 10.1S3.2 14 3.2 8.7A4.7 4.7 0 0 1 12 6.2a4.7 4.7 0 0 1 8.8 2.5Z"></path></svg>';
  actions.appendChild(button);
  li.appendChild(actions);
}

export function setFavoriteLabel(button, on) {
  const label = on ? "Remove from favourites" : "Add to favourites";
  button.classList.toggle("is-active", on);
  button.setAttribute("aria-pressed", on ? "true" : "false");
  button.setAttribute("aria-label", label);
  button.title = label;
}
