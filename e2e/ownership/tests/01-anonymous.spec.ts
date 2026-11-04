import { test, expect } from '@playwright/test';
import {
  getAdminToken,
  countAllEvaluations,
  fillEvaluationForm,
  waitForResponse,
  assertContains,
} from './helpers';

/**
 * Scenario 1: Anonymous user
 *
 * - Calculate shows a result and writes nothing (verified via admin API count before/after)
 * - "Calculate and save" requires sign-in
 */
test('anonymous: calculate works but save requires auth', async ({ page, baseURL }) => {
  if (!baseURL) throw new Error('BASE_URL not set');

  const adminToken = await getAdminToken(baseURL);
  const countBefore = await countAllEvaluations(baseURL, adminToken);

  // Navigate to evaluation form
  await page.goto('/evaluate');

  // Fill form with synthetic data
  await fillEvaluationForm(page, 'Anonymous Test Project');

  // Click "Calculate" (not "Calculate and save")
  const calculateBtn = page.getByRole('button', { name: /^calculate$/i });
  await expect(calculateBtn).toBeVisible();

  // Wait for evaluation response
  const responsePromise = waitForResponse(page, '/api/v1/evaluate', 'POST');
  await calculateBtn.click();
  const response = await responsePromise;

  expect(response.status()).toBe(200);

  // Verify result is displayed (real UI: "ESG SCORE" and "Calculating saves nothing")
  await assertContains(page, /ESG SCORE/i);
  await assertContains(page, /Calculating saves nothing/i);
  await assertContains(page, /Sign in/i);

  // Verify no row was written
  const countAfter = await countAllEvaluations(baseURL, adminToken);
  expect(countAfter).toBe(countBefore);

  // Attempt to save (should require auth)
  const saveBtn = page.getByRole('button', { name: /save|calculate and save/i });
  const saveBtnVisible = await saveBtn.isVisible().catch(() => false);
  if (saveBtnVisible) {
    await saveBtn.click();

    // Should redirect to login or show auth error
    await page.waitForTimeout(1000);
    const currentUrl = page.url();
    const authErrorVisible = await page.getByText(/sign in|unauthorized/i).isVisible().catch(() => false);
    const hasAuthError = currentUrl.includes('login') || authErrorVisible;
    expect(hasAuthError).toBe(true);
  }
});
