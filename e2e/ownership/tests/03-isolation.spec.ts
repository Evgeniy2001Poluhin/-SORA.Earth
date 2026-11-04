import {
  test, expect, apiHeaders,
  API, ADMIN, getAdminToken, createSpecialist, uniqueName, SPECIALIST_PASSWORD, signInUI, signOutUI,
  saveThroughUI, getHistoryIds, getRecord, sessionToken, pdfText,
} from './helpers';

/**
 * Scenario 3: specialist B cannot reach specialist A's record, by History,
 * by id, by CSV or by PDF; the administrator can, and the read is audited.
 */

async function auditDetailEvents(baseURL: string, adminToken: string, evalId: number) {
  const res = await fetch(`${baseURL}${API}/admin/project-access-log?action=detail&resource_id=${evalId}&limit=500`, {
    headers: apiHeaders(adminToken),
  });
  expect(res.status).toBe(200);
  const page = await res.json() as { items: any[]; total: number };
  expect(page.items.length).toBe(page.total);
  return page.items;
}

test('isolation: B sees none of A\'s record; admin reads it and the read is audited', async ({ page, baseURL }) => {
  const adminToken = await getAdminToken(baseURL!);
  const userA = uniqueName('spec_a');
  const userB = uniqueName('spec_b');
  await createSpecialist(baseURL!, adminToken, userA);
  await createSpecialist(baseURL!, adminToken, userB);

  // A saves one evaluation.
  await signInUI(page, userA, SPECIALIST_PASSWORD, '/history');
  const tokenA = (await sessionToken(page))!;
  const nameA = `SpecialistA-Private-${Date.now()}`;
  const evalId = await saveThroughUI(page, nameA);
  const recordBefore = await getRecord(baseURL!, tokenA, evalId);
  expect(recordBefore.status).toBe(200); // positive control: the owner can read it
  expect(recordBefore.body.name).toBe(nameA);
  const allBefore = await getHistoryIds(baseURL!, adminToken);
  expect(allBefore).toContain(evalId);
  expect(await auditDetailEvents(baseURL!, adminToken, evalId)).toEqual([]);
  await signOutUI(page);

  // B: history in the UI and the API holds nothing of A's.
  await signInUI(page, userB, SPECIALIST_PASSWORD, '/history');
  const tokenB = (await sessionToken(page))!;
  await expect(page.getByRole('heading', { level: 1, name: 'My evaluations' })).toBeVisible();
  await expect(page.locator('.cr-empty')).toHaveText('No evaluations match the current filters.');
  await expect(page.locator('.hist-item')).toHaveCount(0);
  await expect(page.getByText(nameA)).toHaveCount(0);
  expect(await getHistoryIds(baseURL!, tokenB)).toEqual([]);

  // B by id: 404, through the API and through the UI's snapshot view.
  const byId = await getRecord(baseURL!, tokenB, evalId);
  expect(byId.status).toBe(404);
  await page.goto(`/evaluate?snapshot=${evalId}`);
  await expect(page.getByRole('region', { name: 'Saved evaluation' }).getByRole('alert'))
    .toHaveText('Evaluation not found or not available.');
  await expect(page.getByText(nameA)).toHaveCount(0);

  // B's CSV export: a header and no rows -- neither A's id nor A's name.
  const csvRes = await fetch(`${baseURL}${API}/export/csv`, { headers: apiHeaders(tokenB) });
  expect(csvRes.status).toBe(200);
  const csv = await csvRes.text();
  const lines = csv.trim().split(/\r?\n/);
  expect(lines[0].startsWith('ID,Name,')).toBe(true);
  expect(lines.slice(1).map((l) => l.split(',')[0])).not.toContain(String(evalId));
  expect(csv).not.toContain(nameA);
  expect(lines).toHaveLength(1);

  // B's refused requests changed nothing: A's record is identical, B owns nothing new.
  expect(await getRecord(baseURL!, tokenA, evalId)).toEqual(recordBefore);
  expect(await getHistoryIds(baseURL!, tokenB)).toEqual([]);
  expect(await getHistoryIds(baseURL!, adminToken)).toEqual(allBefore);
  await signOutUI(page);

  // Admin opens the record by id in the UI; the read is recorded.
  await signInUI(page, ADMIN.username, ADMIN.password, '/mlops');
  await page.goto(`/evaluate?snapshot=${evalId}`);
  await expect(page.getByRole('region', { name: 'Saved evaluation' }).getByRole('heading', { level: 2 })).toHaveText(nameA);

  const events = await auditDetailEvents(baseURL!, adminToken, evalId);
  expect(events).toHaveLength(1);
  expect(events[0]).toMatchObject({ action: 'detail', actor_username: ADMIN.username, actor_role: 'admin', status_code: 200, outcome: 'ok' });
  expect(events[0].targets).toEqual([expect.objectContaining({ resource_id: evalId, effect: 'returned', data_scope: 'pilot' })]);
});

test('saved PDF: owner gets the stored report, B gets 404, anonymous gets 401', async ({ page, baseURL }) => {
  const adminToken = await getAdminToken(baseURL!);
  const userA = uniqueName('spec_a');
  const userB = uniqueName('spec_b');
  await createSpecialist(baseURL!, adminToken, userA);
  await createSpecialist(baseURL!, adminToken, userB);

  await signInUI(page, userA, SPECIALIST_PASSWORD, '/history');
  const tokenA = (await sessionToken(page))!;
  // Non-Latin text, so the parser has to decode the embedded font, not match bytes.
  const nameA = `Проект-А-${Date.now()}`;
  const evalId = await saveThroughUI(page, nameA);
  const record = await getRecord(baseURL!, tokenA, evalId);
  expect(record.status).toBe(200);
  expect(typeof record.body.total_score).toBe('number');
  const allBefore = await getHistoryIds(baseURL!, adminToken);
  await signOutUI(page);

  const pdfUrl = `${baseURL}${API}/history/${evalId}/report.pdf`;
  const ownRes = await fetch(pdfUrl, { headers: apiHeaders(tokenA) });
  expect(ownRes.status).toBe(200);
  expect(ownRes.headers.get('content-type')).toBe('application/pdf');
  const bytes = Buffer.from(await ownRes.arrayBuffer());
  expect(bytes.subarray(0, 5).toString('latin1')).toBe('%PDF-');

  const text = pdfText(bytes);
  expect(text).toContain(`Evaluation ID: ${evalId}`);
  expect(text).toContain(`Project: ${nameA}`);
  const score = text.match(/Total ESG score: ([0-9]+(?:\.[0-9]+)?)\b/);
  expect(score).not.toBeNull();
  expect(Number(score![1])).toBe(record.body.total_score);

  await signInUI(page, userB, SPECIALIST_PASSWORD, '/history');
  const tokenB = (await sessionToken(page))!;
  const bRes = await fetch(pdfUrl, { headers: apiHeaders(tokenB) });
  expect(bRes.status).toBe(404);
  expect(bRes.headers.get('content-type')).not.toBe('application/pdf');

  const anonRes = await fetch(pdfUrl, { headers: apiHeaders() });
  expect(anonRes.status).toBe(401);
  expect(anonRes.headers.get('content-type')).not.toBe('application/pdf');

  // The refused reads changed nothing.
  expect(await getRecord(baseURL!, tokenA, evalId)).toEqual(record);
  expect(await getHistoryIds(baseURL!, tokenB)).toEqual([]);
  expect(await getHistoryIds(baseURL!, adminToken)).toEqual(allBefore);
});
