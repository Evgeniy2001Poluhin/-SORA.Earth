import {
  test, expect,
  getAdminToken, createSpecialist, uniqueName, SPECIALIST_PASSWORD, signInUI,
  fillEvaluationForm, saveButton, isSaveRequest, getHistoryIds, sessionToken, SAVE_URL,
} from './helpers';

/**
 * Scenario 6: a 503 on save says "Save not confirmed", keeps the session and
 * is not retried; once the service answers again, the save succeeds.
 */
test('error handling: a 503 on save is reported, keeps the session and is not retried', async ({ page, baseURL }) => {
  const adminToken = await getAdminToken(baseURL!);
  const userA = uniqueName('spec_a');
  await createSpecialist(baseURL!, adminToken, userA);

  await signInUI(page, userA, SPECIALIST_PASSWORD, '/history');
  const tokenA = (await sessionToken(page))!;
  await page.goto('/evaluate');
  await fillEvaluationForm(page, 'Error Handling Test');

  const saveRequests: string[] = [];
  page.on('request', (req) => { if (isSaveRequest(req)) saveRequests.push(req.url()); });

  // Route and wait registered before the click.
  await page.route(SAVE_URL, (route) => route.fulfill({
    status: 503,
    contentType: 'application/json',
    headers: { 'Retry-After': '5' },
    body: JSON.stringify({ detail: 'The evaluation could not be saved' }),
  }));
  const failed = page.waitForResponse((r) => isSaveRequest(r.request()));
  await saveButton(page).click();
  expect((await failed).status()).toBe(503);

  await expect(page.locator('.ev-save-problem')).toHaveText('Save not confirmed. Check your history before saving again.');
  await expect(page.locator('.ev-save-ok')).toHaveCount(0);

  // The session is kept.
  expect(await sessionToken(page)).toBe(tokenA);
  await expect(page.locator('.shell-user')).toHaveText(`${userA} · specialist`);
  await expect(page.locator('.session-notice')).toHaveCount(0);

  // Not retried.
  await page.waitForTimeout(3000);
  expect(saveRequests).toHaveLength(1);
  expect(await getHistoryIds(baseURL!, tokenA)).toEqual([]);

  // With the service answering again, the same form saves for real.
  await page.unroute(SAVE_URL);
  const saved = page.waitForResponse((r) => isSaveRequest(r.request()));
  await saveButton(page).click();
  const res = await saved;
  expect(res.status()).toBe(201);
  const body = await res.json();
  expect(body.saved).toBe(true);
  await expect(page.locator('.ev-save-ok')).toHaveText(`Saved as evaluation #${body.evaluation_id}. View history`);
  await expect(page.locator('.ev-save-problem')).toHaveCount(0);
  expect(saveRequests).toHaveLength(2);
  expect(await getHistoryIds(baseURL!, tokenA)).toEqual([body.evaluation_id]);
});
