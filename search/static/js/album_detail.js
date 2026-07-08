// album_detail.js — /albums/{id} detail page controller
//
// Two interactions:
//   1. Inline edit form (rename + description) — PATCH /api/albums/{id}.
//   2. Delete button with confirm — DELETE /api/albums/{id}, then
//      navigate back to /albums on success.

const editForm = document.querySelector("[data-album-edit]");
const editStatus = editForm?.querySelector("[data-album-edit-status]");
const deleteForm = document.querySelector("[data-album-delete]");

if (editForm && editStatus) {
  editForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    editStatus.hidden = true;
    editStatus.textContent = "";
    editStatus.classList.remove("status-error");

    const albumId = editForm.dataset.albumEdit;
    const data = new FormData(editForm);
    const name = String(data.get("name") || "").trim();
    const description = String(data.get("description") || "").trim();
    if (!name) {
      showStatus("Name is required.", true);
      return;
    }

    const submit = editForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const resp = await fetch(`/api/albums/${albumId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ name, description }),
      });
      if (!resp.ok) {
        const detail = (await resp.json().catch(() => ({}))).detail
          || `update failed (${resp.status})`;
        showStatus(detail, true);
        return;
      }
      showStatus("Saved.", false);
      // Soft refresh so header + breadcrumb reflect the new name.
      setTimeout(() => window.location.reload(), 400);
    } catch (err) {
      showStatus(err.message || "update failed", true);
    } finally {
      submit.disabled = false;
    }
  });
}

if (deleteForm) {
  deleteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const albumId = deleteForm.dataset.albumDelete;
    const name = deleteForm.dataset.albumName || "this album";
    if (!window.confirm(`Delete album "${name}"? This removes all its memberships and cannot be undone.`)) {
      return;
    }
    const submit = deleteForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const resp = await fetch(`/api/albums/${albumId}`, {
        method: "DELETE",
        headers: { Accept: "application/json" },
      });
      if (!resp.ok && resp.status !== 204) {
        const detail = (await resp.json().catch(() => ({}))).detail
          || `delete failed (${resp.status})`;
        alert(detail);
        submit.disabled = false;
        return;
      }
      window.location.href = "/albums";
    } catch (err) {
      alert(err.message || "delete failed");
      submit.disabled = false;
    }
  });
}

function showStatus(msg, isError) {
  if (!editStatus) return;
  editStatus.textContent = msg;
  editStatus.classList.toggle("status-error", !!isError);
  editStatus.hidden = false;
}