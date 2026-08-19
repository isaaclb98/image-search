import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [sveltekit()],
  // Svelte 5 ships separate server and client runtimes. jsdom is a
  // browser environment, so force the client bundle here. Without it,
  // @testing-library/svelte mounts the server bundle and explodes with
  // "lifecycle_function_unavailable".
  define: {
    'import.meta.env.SSR': 'false'
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      // Proxy API + photo + zip endpoints to FastAPI in dev. In prod,
      // docker-compose does the same routing at the network edge.
      //
      // SvelteKit routes match first when listed in src/routes/. The
      // proxy is only invoked for paths no SvelteKit route handles.
      // We use exact-prefix matches (regex) so /albums/{id} and
      // /photo/{id} (the SvelteKit pages) don't get forwarded to
      // FastAPI, but /albums/{id}/download.zip and /photo/{id}/raw do.
      '/api': {
        target: 'http://localhost:8765',
        changeOrigin: true
      },
      '/healthz': {
        target: 'http://localhost:8765',
        changeOrigin: true
      },
      // Backend photo RAW endpoint must be proxied; SvelteKit pages
      // for /photo/{id} match first because the file route is more
      // specific, but the dev proxy still must serve raw bytes when
      // used directly.
      '^/photo/[^/]+/raw$': {
        target: 'http://localhost:8765',
        changeOrigin: true
      },
      // Zip downloads for the Albums page buttons
      '^/albums/[^/]+/download\\.zip$': {
        target: 'http://localhost:8765',
        changeOrigin: true
      },
      '^/favorites/download\\.zip$': {
        target: 'http://localhost:8765',
        changeOrigin: true
      }
    },
    fs: {
      allow: ['..']
    }
  },
  test: {
    include: ['src/**/*.{test,spec}.{js,ts}'],
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest-setup.ts']
  }
});
