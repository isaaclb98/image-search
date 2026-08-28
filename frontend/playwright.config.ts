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
  webServer: {
    // The dev backend is managed externally — by the nightly-e2e
    // workflow on CI, or by `scripts/dev-qdrant.sh` +
    // `search.dev_server` locally. Playwright's webServer mode
    // spawns a process itself, which we don't want; instead we just
    // connect to whatever's already listening on `url`.
    //
    // To make this work without spawning anything, we use `command:
    // tail -f /dev/null` — a never-exiting process. Combined with
    // `reuseExistingServer: true`, Playwright probes `url` first,
    // sees the dev server, and skips waiting on the tail process.
    // If the dev server is NOT running, the tail process keeps
    // Playwright alive until the 120s timeout, then fails with a
    // clear "server didn't come up" error rather than a vague
    // "webServer exited early".
    command: 'tail -f /dev/null',
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 120_000
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } }
  ]
});
