import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({
      pages: 'build',
      assets: 'build',
      fallback: 'index.html', // SPA fallback for client-side routes
      precompress: false,
      strict: false,
    }),
    // Round-6: View Transitions API crossfade between routes.
    // SvelteKit 2.x doesn't have a `viewTransition` config
    // option (that's a Svelte 5 / future-kit feature), so we
    // implement it via `onNavigate` in +layout.svelte — wraps
    // `goto()` in `document.startViewTransition` when the
    // browser supports it, falls back to a plain navigation
    // otherwise. The CSS rules in global.css drive the
    // actual crossfade animation.
  },
};

export default config;
