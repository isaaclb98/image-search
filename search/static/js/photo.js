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

export const photoPageReady = true;
