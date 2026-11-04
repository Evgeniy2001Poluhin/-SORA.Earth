import {
  test, expect,
  getAdminToken, createSpecialist, uniqueName, SPECIALIST_PASSWORD, signInUI, signOutUI,
  saveThroughUI, projectNameInput, isCalculateRequest, getHistoryIds, sessionToken,
} from './helpers';

/**
 * Scenario 4: signing out ends A's What-If sweep, and after switching to B
 * none of A's form values, results or history are on screen.
 */
test('context switch: sign-out stops A\'s sweep; B sees none of A\'s form, result or history', async ({ page, baseURL }) => {
  const adminToken = await getAdminToken(baseURL!);
  const userA = uniqueName('spec_a');
  const userB = uniqueName('spec_b');
  await createSpecialist(baseURL!, adminToken, userA);
  await createSpecialist(baseURL!, adminToken, userB);

  await signInUI(page, userA, SPECIALIST_PASSWORD, '/history');
  const nameA = `SpecialistA-Unique-${Date.now()}`;
  await saveThroughUI(page, nameA);
  await expect(page.locator('.ev-result h2')).toHaveText(nameA);

  // What-If is a tab of the result card (WhatIf.tsx), not a page of its own.
  await page.getByRole('button', { name: 'What-If', exact: true }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'What-If Sensitivity' })).toBeVisible();

  let heldAndAborted = 0;
  // Each sweep point is a real POST /evaluate, held 1.5 s on its way out so
  // the twelve-point sweep is still running when A signs out. Nothing is
  // mocked. A point the page aborts while it is held cannot be continued;
  // that is the expected outcome for it, not an error of the test.
  await page.route((url) => url.pathname === '/api/v1/evaluate' && !url.searchParams.has('save'), async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    try {
      await route.continue();
    } catch {
      heldAndAborted += 1;
    }
  });

  let signedOut = false;
  let sweepSentBefore = 0;
  const sweepAfter: string[] = [];
  const authorizedAfter: string[] = [];
  const failedAfter: string[] = [];
  page.on('requestfailed', (req) => { if (signedOut && isCalculateRequest(req)) failedAfter.push(req.url()); });
  page.on('request', (req) => {
    if (!signedOut) {
      if (isCalculateRequest(req)) sweepSentBefore += 1;
      return;
    }
    if (isCalculateRequest(req)) sweepAfter.push(req.url());
    if (req.headers()['authorization'] !== undefined) authorizedAfter.push(req.url());
  });

  const firstPoint = page.waitForResponse((r) => isCalculateRequest(r.request()));
  await page.getByRole('button', { name: 'Run sweep', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sweeping…', exact: true })).toBeVisible();
  expect((await firstPoint).status()).toBe(200);
  // The second point is out and held: the loop cannot send a third until it
  // returns, so everything observed from here on was sent after sign-out.
  await expect.poll(() => sweepSentBefore).toBe(2);

  signedOut = true;
  await signOutUI(page);
  expect(sweepSentBefore).toBe(2); // two of twelve points: cut short, not finished

  // Long enough for several more held sweep points, had the loop gone on.
  await page.waitForTimeout(3000);
  expect(sweepAfter).toEqual([]);
  expect(authorizedAfter).toEqual([]);
  // The held second point was aborted by the sign-out, not answered.
  expect(failedAfter).toHaveLength(1);
  expect(heldAndAborted).toBeLessThanOrEqual(1);
  await page.unroute((url) => url.pathname === '/api/v1/evaluate' && !url.searchParams.has('save'));

  // The anonymous page that remains holds nothing of A's.
  expect(page.url()).toMatch(/\/evaluate$/);
  await expect(projectNameInput(page)).toHaveValue('Solar Farm');
  await expect(page.locator('.ev-empty h4')).toHaveText('No evaluation yet');
  await expect(page.getByText(nameA)).toHaveCount(0);

  // B: history, form and result card carry nothing of A's.
  await signInUI(page, userB, SPECIALIST_PASSWORD, '/history');
  const tokenB = (await sessionToken(page))!;
  await expect(page.locator('.cr-empty')).toHaveText('No evaluations match the current filters.');
  await expect(page.locator('.hist-item')).toHaveCount(0);
  await expect(page.getByText(nameA)).toHaveCount(0);
  expect(await getHistoryIds(baseURL!, tokenB)).toEqual([]);

  await page.getByRole('navigation').getByRole('link', { name: 'Evaluate', exact: true }).click();
  await page.waitForURL((u) => u.pathname === '/evaluate');
  await expect(projectNameInput(page)).toHaveValue('Solar Farm');
  await expect(page.locator('.ev-empty h4')).toHaveText('No evaluation yet');
  await expect(page.getByRole('button', { name: 'What-If', exact: true })).toHaveCount(0);
  await expect(page.locator('.ev-save-ok')).toHaveCount(0);
  await expect(page.getByText(nameA)).toHaveCount(0);
});
