import { defineConfig, devices } from '@playwright/test';

/**
 * E2E configuration for project ownership browser tests.
 *
 * Runs against a real PostgreSQL + backend + SPA stack (uvicorn serving the built SPA).
 * No API mocking in the main scenarios.
 */
export default defineConfig({
  testDir: './tests',
  fullyParallel: false, // Sequential execution to avoid DB state conflicts
  forbidOnly: !!process.env.CI,
  retries: 0, // also in CI: a retry would hide a flaky ownership guarantee
  workers: 1, // Single worker to ensure deterministic state
  reporter: [
    ['list'],
    ['json', { outputFile: 'test-results/results.json' }],
  ],

  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:18765',
    // The onboarding tour opens on first visit and covers the login form and the
    // evaluate form. Mark it done before any page loads instead of racing to close it.
    storageState: {
      cookies: [],
      origins: [{
        origin: process.env.BASE_URL || 'http://127.0.0.1:18765',
        localStorage: [{ name: 'sora_tour_done', value: '1' }],
      }],
    },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  timeout: 60000, // 60s per test
  expect: {
    timeout: 10000, // 10s for assertions
  },
});
