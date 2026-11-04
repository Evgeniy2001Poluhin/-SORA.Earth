import { test, expect } from '@playwright/test';
import {
  getAdminToken,
  createSpecialistAccount,
  signInUI,
  signOutUI,
  fillEvaluationForm,
  waitForResponse,
  getHistory,
} from './helpers';

/**
 * Scenario 2: Specialist A saves once even on double-click
 *
 * - Save creates exactly one row (no duplicate on double-click)
 * - After sign-out and sign-in, the record opens from History
 */
test('specialist A: saves once, persists across sessions', async ({ page, baseURL }) => {
  if (!baseURL) throw new Error('BASE_URL not set');

  const adminToken = await getAdminToken(baseURL);
  const username = `specialist_a_${Date.now()}`;
  const password = 'specialista123456';

  // Create account
  await createSpecialistAccount(baseURL, adminToken, username, password);

  // Sign in
  await signInUI(page, username, password);

  // Navigate to evaluation form
  await page.goto('/evaluate');

  // Fill form
  const projectName = `SpecialistA-Project-${Date.now()}`;
  await fillEvaluationForm(page, projectName);

  // Double-click "Calculate and save"
  const saveBtn = page.getByRole('button', { name: /calculate and save|save/i });
  await expect(saveBtn).toBeVisible();

  // Set up request counter
  let saveRequestCount = 0;
  page.on('request', (req) => {
    if (req.url().includes('/api/v1/evaluate') && req.url().includes('save=true')) {
      saveRequestCount++;
    }
  });

  // Double-click (simulate accidental double-click)
  await saveBtn.click();
  await saveBtn.click({ force: true, timeout: 500 }).catch(() => {}); // may be disabled after first click

  // Wait for response
  await waitForResponse(page, '/api/v1/evaluate?save=true', 'POST');

  // Wait a bit to catch any duplicate requests
  await page.waitForTimeout(2000);

  // Verify at most one request was made
  expect(saveRequestCount).toBeLessThanOrEqual(1);

  // Navigate to history
  await page.goto('/history');

  // Verify the project appears exactly once
  const projectRows = page.getByText(projectName);
  await expect(projectRows).toHaveCount(1);

  const savedId = await projectRows.first().getAttribute('data-eval-id') ||
                   await page.locator(`text=${projectName}`).first().locator('..').getAttribute('data-id');

  // Sign out
  await signOutUI(page);

  // Sign in again
  await signInUI(page, username, password);

  // Navigate to history
  await page.goto('/history');

  // Verify the project still appears
  await expect(page.getByText(projectName)).toBeVisible();

  // Click to open detail
  await page.getByText(projectName).click();

  // Verify detail page opens
  await page.waitForURL(`**/history/${savedId || ''}`);
  await expect(page.getByText(projectName)).toBeVisible();
});
