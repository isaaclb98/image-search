// photo.js — detail-page enhancements

const form = document.querySelector("[data-fav-form]");
const button = form?.querySelector(".fav-toggle");
const label = button?.querySelector(".fav-label");

if (form && button && label) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const id = button.dataset.favId;
    if (!id) return;

    const wasOn = button.getAttribute("aria-pressed") === "true";
    setFavorite(!wasOn);
    button.disabled = true;

    try {
      const resp = await fetch(`/api/favorites/${encodeURIComponent(id)}`, {
        method: wasOn ? "DELETE" : "POST",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) throw new Error(`favorite toggle failed: ${resp.status}`);
    } catch (error) {
      console.error(error);
      setFavorite(wasOn);
    } finally {
      button.disabled = false;
    }
  });
}

function setFavorite(on) {
  if (!button || !label) return;
  button.dataset.favState = on ? "on" : "off";
  button.setAttribute("aria-pressed", on ? "true" : "false");
  label.textContent = on ? "favourite" : "add to favourites";
}

const dislikeForm = document.querySelector("[data-dislike-form]");
const dislikeButton = dislikeForm?.querySelector(".dislike-toggle");
const dislikeLabel = dislikeButton?.querySelector(".dislike-label");

if (dislikeForm && dislikeButton && dislikeLabel) {
  dislikeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const id = dislikeButton.dataset.dislikeId;
    if (!id) return;

    const wasOn = dislikeButton.getAttribute("aria-pressed") === "true";
    setDislike(!wasOn);
    dislikeButton.disabled = true;

    try {
      const url = `/api/dislikes/${encodeURIComponent(id)}` +
        (wasOn ? "" : "?source=detail");
      const resp = await fetch(url, {
        method: wasOn ? "DELETE" : "POST",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) throw new Error(`dislike toggle failed: ${resp.status}`);
    } catch (error) {
      console.error(error);
      setDislike(wasOn);
    } finally {
      dislikeButton.disabled = false;
    }
  });
}

function setDislike(on) {
  if (!dislikeButton || !dislikeLabel) return;
  dislikeButton.dataset.dislikeState = on ? "on" : "off";
  dislikeButton.setAttribute("aria-pressed", on ? "true" : "false");
  dislikeLabel.textContent = on ? "Disliked" : "Dislike";
}

export const photoPageReady = true;
