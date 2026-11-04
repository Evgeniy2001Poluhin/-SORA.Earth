import { test, expect } from '@playwright/test';
import {
  getAdminToken,
  createSpecialistAccount,
  getUserId,
  blockUser,
  signInUI,
  fillEvaluationForm,
  assertContains,
} from './helpers';

/**
 * Scenario 5: Revocation
 *
 * - Admin blocks specialist A while A is signed in
 * - A's next action ends the session with "session has expired" notice
 * - No anonymous retry of that request (assert no request without Authorization to that path)
 */
test('revocation: blocking A invalidates their session immediately', async ({ page, baseURL, request }) => {
  if (!baseURL) throw new Error('BASE_URL not set');

  const adminToken = await getAdminToken(baseURL);
  const username = `specialist_a_${Date.now()}`;
  const password = 'specialista123456';

  await createSpecialistAccount(baseURL, adminToken, username, password);

  // Sign in as specialist A
  await signInUI(page, username, password);

  // Navigate to evaluate page
  await page.goto('/evaluate');
  await fillEvaluationForm(page, 'Revocation Test Project');

  // Set up request interceptor to catch unauthorized requests
  const unauthorizedRequests: string[] = [];
  page.on('request', (req) => {
    const authHeader = req.headers()['authorization'];
    if (!authHeader && req.url().includes('/api/v1/evaluate')) {
      unauthorizedRequests.push(req.url());
    }
  });

  // Get user ID
  const userId = await getUserId(baseURL, adminToken, username);
  if (!userId) throw new Error('User ID not found');

  // Block the user while they are signed in
  await blockUser(baseURL, adminToken, userId);

  // Wait a moment for the block to propagate
  await page.waitForTimeout(500);

  // Try to save (this should fail with session expired)
  const saveBtn = page.getByRole('button', { name: /calculate and save|save/i });
  await saveBtn.click();

  // Wait for error response
  await page.waitForResponse((resp) =>
    resp.url().includes('/api/v1/evaluate') && resp.status() === 401,
    { timeout: 5000 }
  );

  // Verify "session expired" notice appears
  await assertContains(page, /session.*expired|unauthorized|please.*sign.*in/i, { timeout: 5000 });

  // Verify the session is cleared (real session is sessionStorage["sora.token"])
  const tokenAfterBlock = await page.evaluate(() => sessionStorage.getItem('sora.token'));
  expect(tokenAfterBlock).toBeNull();

  // Verify no anonymous retry of the blocked request
  await page.waitForTimeout(2000);
  expect(unauthorizedRequests).toHaveLength(0);

  // Verify subsequent navigation redirects to login or shows auth requirement
  await page.goto('/history');
  const currentUrl = page.url();
  const isAuthRequired = currentUrl.includes('login') ||
                        await page.getByText(/sign in|unauthorized/i).isVisible({ timeout: 2000 });
  expect(isAuthRequired).toBe(true);
});
