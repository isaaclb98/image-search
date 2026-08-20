// SPA mode — fully client-rendered. The SvelteKit build emits a single
// index.html plus hashed assets in /_app/. The FastAPI backend mounts
// /_app/ as static files and falls back to index.html for every other
// path so the SPA can hydrate and route client-side via pushState.
export const prerender = false;
export const ssr = false;
export const trailingSlash = 'never';
