import { test, expect } from '@playwright/test';
import {
  getAdminToken,
  createSpecialistAccount,
  signInUI,
  signOutUI,
  fillEvaluationForm,
  assertNotContains,
} from './helpers';

/**
 * Scenario 4: Switching A→B and sign-out clears all A's context
 *
 * - None of A's results, form values or history are visible after switch to B
 * - A What-If sweep started by A sends no further requests after sign-out
 */
test('context switch: A→B clears all A data, stops in-flight requests', async ({ page, baseURL }) => {
  if (!baseURL) throw new Error('BASE_URL not set');

  const adminToken = await getAdminToken(baseURL);
  const timestamp = Date.now();
  const usernameA = `specialist_a_${timestamp}`;
  const usernameB = `specialist_b_${timestamp}`;

  await createSpecialistAccount(baseURL, adminToken, usernameA, 'specialista123456');
  await createSpecialistAccount(baseURL, adminToken, usernameB, 'specialistb123456');

  // ===== SPECIALIST A: Save a project, start a What-If sweep =====
  await signInUI(page, usernameA, 'specialista123456');

  await page.goto('/evaluate');
  const projectNameA = `SpecialistA-Unique-${Date.now()}`;
  await fillEvaluationForm(page, projectNameA);

  // Save
  await page.getByRole('button', { name: /calculate and save|save/i }).click();
  await page.waitForResponse((resp) =>
    resp.url().includes('/api/v1/evaluate') && resp.url().includes('save=true')
  );

  // Navigate to What-If page - required, do not skip
  const whatIfLink = page.getByRole('link', { name: /what-if|scenario/i });
  await expect(whatIfLink).toBeVisible({ timeout: 5000 });
  await whatIfLink.click();

  // Start a sweep - track requests to verify they stop after sign-out
  const requestsAfterSignOut: string[] = [];
  let signedOut = false;

  page.on('request', (req) => {
    if (signedOut && req.url().includes('/api/v1')) {
      requestsAfterSignOut.push(req.url());
    }
  });

  const sweepBtn = page.getByRole('button', { name: /run sweep|start|analyze/i });
  await expect(sweepBtn).toBeVisible({ timeout: 5000 });

  // Start the sweep
  await sweepBtn.click();

  // Wait for the sweep to actually start (at least one request)
  await page.waitForResponse((resp) =>
    resp.url().includes('/api/v1/') && !resp.url().includes('/auth/'),
    { timeout: 5000 }
  );

  // Sign out while sweep is running
  signedOut = true;
  await signOutUI(page);

  // Wait to ensure no more requests are sent after sign-out
  await page.waitForTimeout(3000);

  // Verify no API requests after sign-out (except auth/health)
  const unauthorizedRequests = requestsAfterSignOut.filter(url =>
    !url.includes('/auth/') && !url.includes('/health')
  );
  expect(unauthorizedRequests.length).toBe(0);

  // ===== SPECIALIST B: Sign in and verify no A data visible =====
  await signInUI(page, usernameB, 'specialistb123456');

  // Check History - should NOT show A's project
  await page.goto('/history');
  await assertNotContains(page, projectNameA, { timeout: 3000 });

  // Check Evaluate form - should NOT show A's form values
  await page.goto('/evaluate');
  const projectNameInput = page.getByLabel(/project name/i);
  await expect(projectNameInput).toHaveValue('');

  // Check that any cached results are cleared
  const resultSection = page.locator('[data-testid="evaluation-result"]');
  if (await resultSection.isVisible({ timeout: 1000 }).catch(() => false)) {
    // Should not show A's project name in results
    await assertNotContains(page, projectNameA);
  }
});
