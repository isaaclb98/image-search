import { defineConfig, devices } from '@playwright/test';

const PORT = process.env.PLAYWRIGHT_PORT ?? '8000';
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure'
  },
  // The dev backend is managed by the workflow itself (or by
  // scripts/dev-qdrant.sh + search.dev_server locally) — it
  // runs as a long-lived process that the runner keeps alive
  // for the duration of the e2e step. Playwright's webServer
  // mode would either: (a) try to spawn the server itself
  // (wrong — the workflow already does), or (b) wait for a
  // placeholder command to "make the URL available", which
  // doesn't happen with a no-op `tail -f` even when the URL
  // is already up. Omit webServer entirely so Playwright just
  // connects to whatever's listening on `baseURL`.
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } }
  ]
});
