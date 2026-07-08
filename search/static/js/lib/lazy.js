// lib/lazy.js — IntersectionObserver-based image loader
//
// v1 uses native `loading="lazy"` on the HTML <img>, so this module
// is unused today. It's kept for v1.1 when we add a thumbnail
// endpoint — at that point we'll want JS-driven lazy loading for
// tighter control over decode timing and a per-page quota.

export function observeImages(root) {
  if (!("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          const img = entry.target;
          if (img.dataset.src) {
            img.src = img.dataset.src;
            img.removeAttribute("data-src");
          }
          io.unobserve(img);
        }
      }
    },
    { rootMargin: "200px" }
  );
  for (const img of root.querySelectorAll("img[data-src]")) {
    io.observe(img);
  }
}
