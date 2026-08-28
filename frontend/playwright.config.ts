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
    // The dev backend is managed by the test runner (see
    // .github/workflows/nightly-e2e.yml and the local
    // `scripts/dev-qdrant.sh` + `search.dev_server` flow), so
    // playwright never needs to start one itself. `reuseExistingServer`
    // + the placeholder command let the runner pick up the running
    // server on `url` without forking one. The command is a no-op
    // because Playwright short-circuits before invoking it.
    command: 'echo "(dev server managed externally; see nightly-e2e.yml)"',
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 120_000
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } }
  ]
});
