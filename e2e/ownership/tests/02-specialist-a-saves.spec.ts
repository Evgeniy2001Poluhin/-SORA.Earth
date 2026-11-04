import {
  test, expect, apiHeaders,
  getAdminToken, createSpecialist, uniqueName, SPECIALIST_PASSWORD, signInUI, signOutUI,
  fillEvaluationForm, saveButton, isSaveRequest, getHistory, getHistoryIds, sessionToken,
} from './helpers';

/**
 * Scenario 2: specialist A saves once even on a double-click, and the record
 * opens from History after signing out and in again.
 */
test('specialist A: double-click saves exactly once; the record reopens after a new sign-in', async ({ page, baseURL }) => {
  const adminToken = await getAdminToken(baseURL!);
  const username = uniqueName('spec_a');
  await createSpecialist(baseURL!, adminToken, username);
  const allBefore = await getHistoryIds(baseURL!, adminToken);

  await signInUI(page, username, SPECIALIST_PASSWORD, '/history');
  const tokenA = (await sessionToken(page))!;
  expect(await getHistoryIds(baseURL!, tokenA)).toEqual([]);

  await page.goto('/evaluate');
  const projectName = `SpecialistA-Project-${Date.now()}`;
  await fillEvaluationForm(page, projectName);

  const saveRequests: string[] = [];
  page.on('request', (req) => { if (isSaveRequest(req)) saveRequests.push(req.url()); });
  const saved = page.waitForResponse((r) => isSaveRequest(r.request()));

  // A real double-click on the one "Calculate and save" button.
  await saveButton(page).dblclick();
  const res = await saved;
  expect(res.status()).toBe(201);
  const body = await res.json();
  expect(body.saved).toBe(true);
  const evalId: number = body.evaluation_id;
  expect(Number.isSafeInteger(evalId) && evalId > 0).toBe(true);
  await expect(page.locator('.ev-save-ok')).toContainText(`Saved as evaluation #${evalId}.`);

  // Give a second request every chance to appear, then count.
  await page.waitForTimeout(1500);
  expect(saveRequests).toHaveLength(1);

  // Exactly one new row in the whole table, and it is this id, owned by A.
  const allAfter = await getHistoryIds(baseURL!, adminToken);
  expect(allAfter.filter((id) => !allBefore.includes(id))).toEqual([evalId]);
  const mine = await getHistory(baseURL!, tokenA);
  expect(mine.map((r) => r.id)).toEqual([evalId]);
  expect(mine[0].name).toBe(projectName);

  // History lists exactly one row.
  await page.goto('/history');
  await expect(page.locator('.hist-item')).toHaveCount(1);

  await signOutUI(page);
  await signInUI(page, username, SPECIALIST_PASSWORD, '/history');

  // The single row opens the saved record by its id.
  await expect(page.locator('.hist-item')).toHaveCount(1);
  await page.locator('.hist-item').click();
  await page.waitForURL((u) => u.pathname === '/evaluate' && u.searchParams.get('snapshot') === String(evalId));
  const card = page.getByRole('region', { name: 'Saved evaluation' });
  await expect(card.getByRole('heading', { level: 2 })).toHaveText(projectName);
  await expect(card.getByRole('alert')).toHaveCount(0);
});
