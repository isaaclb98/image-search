// albums.js — /albums index page controller
//
// Single concern: the create-album form. Submitting the form
// POSTs to /api/albums, optimistically prepends the new album
// card to the list, and clears the form. Errors from the API
// surface inline next to the submit button.

const form = document.querySelector("[data-album-create]");
const errorEl = form?.querySelector("[data-album-error]");

if (form && errorEl) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorEl.hidden = true;
    errorEl.textContent = "";

    const data = new FormData(form);
    const name = String(data.get("name") || "").trim();
    const description = String(data.get("description") || "").trim();
    if (!name) {
      showError("Name is required.");
      return;
    }

    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const resp = await fetch("/api/albums", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ name, description }),
      });
      if (!resp.ok) {
        const detail = (await resp.json().catch(() => ({}))).detail
          || `create failed (${resp.status})`;
        showError(detail);
        return;
      }
      const album = await resp.json();
      // Reload rather than render-in-place. The card markup
      // duplicates the server template; re-fetching keeps the
      // SSR rendering shape canonical and avoids two code paths
      // for the same visual.
      window.location.reload();
    } catch (err) {
      showError(err.message || "create failed");
    } finally {
      submit.disabled = false;
    }
  });
}

function showError(msg) {
  if (!errorEl) return;
  errorEl.textContent = msg;
  errorEl.hidden = false;
}