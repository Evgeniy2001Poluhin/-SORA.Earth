import {
  test, expect, apiHeaders,
  getAdminToken, getHistoryIds, countPredictionLog, fillEvaluationForm,
  calculateButton, saveButton, isCalculateRequest, isSaveRequest,
} from './helpers';

/**
 * Scenario 1: anonymous.
 * Calculate shows a result and writes nothing; saving is not offered and is
 * pointed at sign-in instead.
 */
test('anonymous: calculate shows a result, writes nothing, save requires sign-in', async ({ page, baseURL }) => {
  const adminToken = await getAdminToken(baseURL!);
  const idsBefore = await getHistoryIds(baseURL!, adminToken);
  const logBefore = await countPredictionLog(baseURL!, adminToken);

  const saveRequests: string[] = [];
  page.on('request', (req) => { if (isSaveRequest(req)) saveRequests.push(req.url()); });

  await page.goto('/evaluate');
  await expect(page.locator('header').getByRole('link', { name: 'Sign in', exact: true })).toBeVisible();
  await fillEvaluationForm(page, 'Anonymous Test Project');

  // Saving is not offered to an anonymous visitor, only pointed at sign-in.
  await expect(saveButton(page)).toHaveCount(0);
  const note = page.locator('.ev-save-note', { hasText: 'Sign in to save evaluations.' });
  await expect(note).toHaveText('Sign in to save evaluations. Calculating saves nothing.');
  await expect(note.getByRole('link', { name: 'Sign in', exact: true })).toHaveAttribute('href', '/login?next=%2Fevaluate');

  const calculated = page.waitForResponse((r) => isCalculateRequest(r.request()));
  await calculateButton(page).click();
  const res = await calculated;
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.saved).toBe(false);
  expect(body.evaluation_id).toBeNull();
  expect(res.request().headers()['authorization']).toBeUndefined();

  await expect(page.locator('.ev-score-sub')).toHaveText('/ 100 · ESG SCORE');
  await expect(page.locator('.ev-result h2')).toHaveText('Anonymous Test Project');
  await expect(page.locator('.ev-save-ok')).toHaveCount(0);

  // Nothing was written: the same evaluation ids and the same log size.
  expect(await getHistoryIds(baseURL!, adminToken)).toEqual(idsBefore);
  expect(await countPredictionLog(baseURL!, adminToken)).toBe(logBefore);
  expect(saveRequests).toEqual([]);

  // The sign-in link from the note leads to the login page and back to /evaluate.
  await note.getByRole('link', { name: 'Sign in', exact: true }).click();
  await page.waitForURL((u) => u.pathname === '/login' && u.searchParams.get('next') === '/evaluate');
  await expect(page.getByRole('heading', { level: 1, name: 'Sign in', exact: true })).toBeVisible();
});
