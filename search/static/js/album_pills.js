// album_pills.js — photo detail page album toggles
//
// Each pill toggles this photo's membership in one album.
// Membership is independent of the favourites toggle on the
// same page — favouriting doesn't add the photo to any album,
// and being in an album doesn't favourite the photo. So the
// two controls share a UI page but have separate state and
// separate endpoints.
//
// Click → optimistic visual flip → POST/DELETE the membership
// endpoint → revert on error. Errors are surfaced in the
// pill's title attribute (tooltip) so we don't need a
// dedicated error region for what is a single-click action.

const pills = Array.from(document.querySelectorAll("[data-album-pill]"));

for (const pill of pills) {
  pill.addEventListener("click", async () => {
    const albumId = pill.dataset.albumPill;
    const favId = pill.dataset.favId;
    if (!albumId || !favId) return;

    const wasOn = pill.dataset.albumPillState === "on";
    const nextOn = !wasOn;
    // Optimistic flip
    setPillState(pill, nextOn);
    pill.disabled = true;

    try {
      const resp = await fetch(
        `/api/albums/${albumId}/members/${encodeURIComponent(favId)}`,
        {
          method: nextOn ? "POST" : "DELETE",
          headers: { Accept: "application/json" },
        }
      );
      if (!resp.ok && resp.status !== 204) {
        throw new Error(`toggle failed (${resp.status})`);
      }
    } catch (err) {
      console.error(err);
      // Revert
      setPillState(pill, wasOn);
      pill.title = err.message || "toggle failed";
      // Clear the error title on the next interaction
      pill.addEventListener(
        "click",
        () => { pill.title = ""; },
        { once: true }
      );
    } finally {
      pill.disabled = false;
    }
  });
}

function setPillState(pill, on) {
  pill.dataset.albumPillState = on ? "on" : "off";
  pill.setAttribute("aria-pressed", on ? "true" : "false");
  pill.classList.toggle("album-pill--on", on);
}