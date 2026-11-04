import { test, expect } from '@playwright/test';
import {
  getAdminToken,
  createSpecialistAccount,
  signInUI,
  fillEvaluationForm,
  assertContains,
} from './helpers';

/**
 * Scenario 6: Error handling
 *
 * - A 503 shows "Save not confirmed"
 * - Keeps the session (no sign-out)
 * - Is not retried automatically
 */
test('error handling: 503 shows error, keeps session, no auto-retry', async ({ page, baseURL }) => {
  if (!baseURL) throw new Error('BASE_URL not set');

  const adminToken = await getAdminToken(baseURL);
  const username = `specialist_a_${Date.now()}`;
  const password = 'specialista123456';

  await createSpecialistAccount(baseURL, adminToken, username, password);

  // Sign in
  await signInUI(page, username, password);

  // Navigate to evaluate page
  await page.goto('/evaluate');
  await fillEvaluationForm(page, 'Error Handling Test');

  // Track save requests (count via request event only, not in route handler)
  let saveRequestCount = 0;
  page.on('request', (req) => {
    if (req.url().includes('/api/v1/evaluate') && req.url().includes('save=true')) {
      saveRequestCount++;
    }
  });

  // Intercept save request and return 503 (do NOT increment counter here - already counted above)
  await page.route('**/api/v1/evaluate?save=true', (route) => {
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: 'Service temporarily unavailable',
        reason_code: 'database_unavailable',
      }),
    });
  });

  // Click save
  const saveBtn = page.getByRole('button', { name: /calculate and save|save/i });
  await saveBtn.click();

  // Wait for error message to appear
  await assertContains(page, /save.*not.*confirmed|error.*saving|service.*unavailable/i, { timeout: 5000 });

  // Verify session is still active (check for user menu or sign-out button)
  const signOutBtn = page.getByRole('button', { name: /sign out|logout/i });
  await expect(signOutBtn).toBeVisible({ timeout: 2000 });

  // Verify token is still in sessionStorage (real session is sessionStorage["sora.token"])
  const tokenAfterError = await page.evaluate(() => sessionStorage.getItem('sora.token'));
  expect(tokenAfterError).toBeTruthy();

  // Wait to ensure no automatic retry
  await page.waitForTimeout(3000);
  expect(saveRequestCount).toBe(1); // Only the initial request, no retry

  // Verify user can navigate normally (session intact)
  await page.goto('/history');
  const currentUrl = page.url();
  expect(currentUrl).toContain('/history');

  // Remove route interception and verify normal operation works
  await page.unroute('**/api/v1/evaluate?save=true');

  await page.goto('/evaluate');
  await fillEvaluationForm(page, 'After Error Recovery');

  // This should succeed now
  await saveBtn.click();
  await page.waitForResponse((resp) =>
    resp.url().includes('/api/v1/evaluate') && resp.url().includes('save=true') && resp.ok(),
    { timeout: 5000 }
  );

  // Verify success (adjust based on actual success UI)
  await assertContains(page, /saved|success/i, { timeout: 5000 });
});
