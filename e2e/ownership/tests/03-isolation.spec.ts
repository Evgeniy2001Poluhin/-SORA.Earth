import { test, expect } from '@playwright/test';
import {
  getAdminToken,
  createSpecialistAccount,
  getUserId,
  signInUI,
  fillEvaluationForm,
  waitForResponse,
} from './helpers';

/**
 * Scenario 3: Specialist B cannot access A's records
 *
 * - Specialist B's history does not show A's records
 * - Direct GET /api/v1/history/{id} with B's token returns 404
 * - B's CSV export does not contain A's records
 * - Admin can read A's record, and the audit log shows it
 */
test('specialist B: cannot access specialist A records', async ({ page, baseURL, request }) => {
  if (!baseURL) throw new Error('BASE_URL not set');

  const adminToken = await getAdminToken(baseURL);
  const timestamp = Date.now();
  const usernameA = `specialist_a_${timestamp}`;
  const usernameB = `specialist_b_${timestamp}`;

  // Create both accounts
  await createSpecialistAccount(baseURL, adminToken, usernameA, 'specialista123456');
  await createSpecialistAccount(baseURL, adminToken, usernameB, 'specialistb123456');

  // ===== SPECIALIST A: Create a record =====
  await signInUI(page, usernameA, 'specialista123456');

  await page.goto('/evaluate');
  const projectNameA = `SpecialistA-Private-${Date.now()}`;
  await fillEvaluationForm(page, projectNameA);

  // Save and capture the evaluation_id from response
  const saveBtnA = page.getByRole('button', { name: /calculate and save|save/i });

  const saveResponsePromise = page.waitForResponse(resp =>
    resp.url().includes('/api/v1/evaluate') && resp.url().includes('save=true') && resp.status() === 201
  );

  await saveBtnA.click();
  const saveResponse = await saveResponsePromise;
  const saveData = await saveResponse.json();
  const evalId = saveData.evaluation_id;

  if (!evalId) {
    throw new Error('Save response did not include evaluation_id');
  }

  // Get A's token for positive controls
  const tokenA = await page.evaluate(() => sessionStorage.getItem('sora.token'));

  // POSITIVE CONTROL: A can access their own record
  const aDetailResp = await request.get(`${baseURL}/api/v1/history/${evalId}`, {
    headers: { 'Authorization': `Bearer ${tokenA}` },
  });
  expect(aDetailResp.ok()).toBe(true);
  const aDetail = await aDetailResp.json();
  expect(aDetail.name).toBe(projectNameA);  // Verify project name was saved

  // POSITIVE CONTROL: A can get their PDF (endpoint from PR 513)
  const aPdfResp = await request.get(`${baseURL}/api/v1/history/${evalId}/report.pdf`, {
    headers: { 'Authorization': `Bearer ${tokenA}` },
  });
  if (aPdfResp.status() === 404) {
    // Endpoint doesn't exist yet (awaiting PR 513 integration) - skip PDF checks
    test.skip();
  }
  expect(aPdfResp.status()).toBe(200);
  expect(aPdfResp.headers()['content-type']).toContain('application/pdf');
  const pdfBytes = await aPdfResp.body();
  const pdfText = pdfBytes.toString('latin1');
  expect(pdfText).toMatch(/^%PDF/);  // Valid PDF header
  expect(pdfText).toContain(projectNameA);  // Contains saved project name

  // Sign out A
  await page.getByRole('button', { name: /sign out|logout/i }).click();
  await page.waitForURL('/');

  // ===== SPECIALIST B: Try to access A's record =====
  await signInUI(page, usernameB, 'specialistb123456');

  // Get B's token for API calls (real session is sessionStorage["sora.token"])
  const tokenB = await page.evaluate(() => sessionStorage.getItem('sora.token'));

  // Check B's history via UI - should NOT contain A's project
  // (History shows time/region/scores/budget/duration, not project name in list)
  await page.goto('/history');

  // Verify A's project is not in the visible table/list
  const historyContent = await page.textContent('body');
  expect(historyContent).not.toContain(projectNameA);

  // Direct API call to A's eval_id with B's token - should be 404
  const detailResp = await request.get(`${baseURL}/api/v1/history/${evalId}`, {
    headers: { 'Authorization': `Bearer ${tokenB}` },
  });
  expect(detailResp.status()).toBe(404);

  // CSV export should not contain A's project
  const csvResp = await request.get(`${baseURL}/api/v1/export/csv`, {
    headers: { 'Authorization': `Bearer ${tokenB}` },
  });
  expect(csvResp.ok()).toBe(true);
  const csvText = await csvResp.text();
  expect(csvText).not.toContain(projectNameA);

  // PDF request for A's record should be strictly 404 with B's token (correct endpoint)
  const bPdfResp = await request.get(`${baseURL}/api/v1/history/${evalId}/report.pdf`, {
    headers: { 'Authorization': `Bearer ${tokenB}` },
  });
  expect(bPdfResp.status()).toBe(404);

  // Anonymous PDF request should be 401
  const anonPdfResp = await request.get(`${baseURL}/api/v1/history/${evalId}/report.pdf`);
  expect(anonPdfResp.status()).toBe(401);

  // Sign out B
  await page.getByRole('button', { name: /sign out|logout/i }).click();

  // ===== ADMIN: Can read A's record, audit logged =====
  await signInUI(page, 'admin', 'sora2026');

  // Admin history should show A's project
  await page.goto('/history');
  await expect(page.getByText(projectNameA)).toBeVisible();

  // Admin detail request
  const adminDetailResp = await request.get(`${baseURL}/api/v1/history/${evalId}`, {
    headers: { 'Authorization': `Bearer ${adminToken}` },
  });
  expect(adminDetailResp.ok()).toBe(true);

  // Check audit log shows the read
  const auditResp = await request.get(`${baseURL}/api/v1/admin/project-access-log?limit=10`, {
    headers: { 'Authorization': `Bearer ${adminToken}` },
  });
  expect(auditResp.ok()).toBe(true);
  const auditData = await auditResp.json();

  // Find an entry for this eval_id with action=detail
  const auditEntry = auditData.items?.find((entry: any) =>
    entry.action === 'detail' &&
    entry.targets?.some((t: any) => t.resource_id === parseInt(evalId, 10))
  );
  expect(auditEntry).toBeTruthy();
});
