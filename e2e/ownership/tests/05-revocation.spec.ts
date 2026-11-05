import {
  test, expect,
  getAdminToken, createSpecialist, uniqueName, SPECIALIST_PASSWORD, signInUI, blockUser,
  fillEvaluationForm, saveButton, isSaveRequest, getHistoryIds, countPredictionLog, sessionToken,
} from './helpers';

/**
 * Scenario 5: the administrator blocks A while A is signed in. A's next save
 * is refused with 401, the session ends with the expiry notice, the request
 * is not repeated without a Bearer, and nothing was written.
 */
test('revocation: a blocked specialist\'s next save ends the session and writes nothing', async ({ page, baseURL }) => {
  const adminToken = await getAdminToken(baseURL!);
  const userA = uniqueName('spec_a');
  const userId = await createSpecialist(baseURL!, adminToken, userA);

  await signInUI(page, userA, SPECIALIST_PASSWORD, '/history');
  const tokenA = (await sessionToken(page))!;
  await page.goto('/evaluate');
  await fillEvaluationForm(page, 'Revocation Test Project');

  const idsBefore = await getHistoryIds(baseURL!, adminToken);
  const logBefore = await countPredictionLog(baseURL!, adminToken);

  const saveAuth: Array<string | undefined> = [];
  page.on('request', (req) => { if (isSaveRequest(req)) saveAuth.push(req.headers()['authorization']); });

  await blockUser(baseURL!, adminToken, userId);

  const refused = page.waitForResponse((r) => isSaveRequest(r.request()));
  await saveButton(page).click();
  expect((await refused).status()).toBe(401);

  const notice = page.locator('.session-notice');
  await expect(notice).toHaveText('Your session has expired. Sign in again. Sign in');
  await expect(notice).toHaveAttribute('role', 'alert');
  await expect(notice.getByRole('link', { name: 'Sign in', exact: true })).toHaveAttribute('href', '/login?next=%2Fevaluate');
  expect(await sessionToken(page)).toBeNull();
  await expect(page.locator('.shell-user')).toHaveCount(0);
  await expect(saveButton(page)).toHaveCount(0);

  // No anonymous retry: one save request, and it carried A's Bearer.
  await page.waitForTimeout(2000);
  expect(saveAuth).toEqual([`Bearer ${tokenA}`]);

  // The refused save wrote nothing.
  expect(await getHistoryIds(baseURL!, adminToken)).toEqual(idsBefore);
  expect(await countPredictionLog(baseURL!, adminToken)).toBe(logBefore);
});
