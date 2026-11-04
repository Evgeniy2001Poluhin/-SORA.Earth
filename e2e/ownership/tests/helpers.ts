import { Page, Request, test as base, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

export const API = '/api/v1';

/*
 * Client addresses. app/rate_limit.py allows 100 requests a minute per
 * address and counts every request, SPA assets included; behind a trusted
 * proxy (127.0.0.0/8 is one) it takes the address from X-Real-IP, which is
 * what nginx sets in production. Every request of this suite comes from one
 * loopback address, so the suite would be refused with 429 part-way through
 * a scenario. Each person in a scenario -- anonymous visitor, A, B, the
 * administrator, and the API helpers -- gets an address of their own, which
 * is what separate people behind the real proxy look like. The limiter
 * itself is not under test here.
 */
const SALT = Math.floor(Math.random() * 200) + 20;
let addressCounter = 0;
export function freshAddress(): string {
  addressCounter += 1;
  return `10.${SALT}.${(addressCounter >> 8) & 255}.${addressCounter & 255}`;
}
let apiAddress = freshAddress();

/** Every spec imports `test` from here: each test starts with fresh addresses. */
export const test = base.extend<{ clientAddresses: void }>({
  clientAddresses: [async ({ page }, use) => {
    apiAddress = freshAddress();
    await page.context().setExtraHTTPHeaders({ 'X-Real-IP': freshAddress() });
    await use();
  }, { auto: true }],
});
export { expect };

/** Headers for a helper's own API call. */
function h(token?: string, json = false): Record<string, string> {
  const headers: Record<string, string> = { 'X-Real-IP': apiAddress };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (json) headers['Content-Type'] = 'application/json';
  return headers;
}
export const apiHeaders = h;
export const ADMIN = { username: 'admin', password: 'sora2026' }; // scripts/bootstrap_users.py --dev
export const SPECIALIST_PASSWORD = 'specialist-e2e-password'; // app/accounts.py PASSWORD_MIN = 15

/** A fresh username that matches app/accounts.py USERNAME_PATTERN. */
export function uniqueName(prefix: string): string {
  return `${prefix}_${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
}

async function expectJson<T>(res: Response, what: string, expected: number): Promise<T> {
  if (res.status !== expected) {
    throw new Error(`${what}: expected ${expected}, got ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as T;
}

export async function apiLogin(baseURL: string, username: string, password: string): Promise<string> {
  const res = await fetch(`${baseURL}${API}/auth/login-json`, {
    method: 'POST',
    headers: h(undefined, true),
    body: JSON.stringify({ username, password }),
  });
  return (await expectJson<{ access_token: string }>(res, `login ${username}`, 200)).access_token;
}

export const getAdminToken = (baseURL: string) => apiLogin(baseURL, ADMIN.username, ADMIN.password);

/** Create a specialist through the admin API. The name is fresh, so only 201 is accepted. */
export async function createSpecialist(baseURL: string, adminToken: string, username: string): Promise<string> {
  const res = await fetch(`${baseURL}${API}/admin/users`, {
    method: 'POST',
    headers: h(adminToken, true),
    body: JSON.stringify({ username, password: SPECIALIST_PASSWORD, role: 'specialist' }),
  });
  const account = await expectJson<{ user_id: string; role: string }>(res, `create ${username}`, 201);
  expect(account.role).toBe('specialist');
  return account.user_id;
}

export async function blockUser(baseURL: string, adminToken: string, userId: string): Promise<void> {
  const res = await fetch(`${baseURL}${API}/admin/users/${userId}`, {
    method: 'PATCH',
    headers: h(adminToken, true),
    body: JSON.stringify({ is_blocked: true }),
  });
  const account = await expectJson<{ is_blocked: boolean }>(res, 'block user', 200);
  expect(account.is_blocked).toBe(true);
}

export interface HistoryRow {
  id: number;
  name: string | null;
  total_score: number | null;
  budget: number | null;
  owner_user_id?: string | null;
  data_scope?: string | null;
}

/** GET /history as the token's account: every row it may read, in one page. */
export async function getHistory(baseURL: string, token: string): Promise<HistoryRow[]> {
  const res = await fetch(`${baseURL}${API}/history?limit=500`, { headers: h(token) });
  const page = await expectJson<{ items: HistoryRow[]; total: number }>(res, 'GET /history', 200);
  // The page must hold the whole set, or an id check over it proves nothing.
  expect(page.items.length).toBe(page.total);
  return page.items;
}

export async function getHistoryIds(baseURL: string, token: string): Promise<number[]> {
  return (await getHistory(baseURL, token)).map((r) => r.id);
}

export async function getRecord(baseURL: string, token: string, id: number): Promise<{ status: number; body: any }> {
  const res = await fetch(`${baseURL}${API}/history/${id}`, { headers: h(token) });
  return { status: res.status, body: res.status === 200 ? await res.json() : await res.text() };
}

/** Rows in predictions_log as the administrator reads it (every scope). */
export async function countPredictionLog(baseURL: string, adminToken: string): Promise<number> {
  const res = await fetch(`${baseURL}${API}/analytics/predictions-log?limit=1000`, {
    headers: h(adminToken),
  });
  const rows = await expectJson<unknown[]>(res, 'GET predictions-log', 200);
  expect(rows.length).toBeLessThan(1000); // under the cap, so the count is the whole table
  return rows.length;
}

export async function sessionToken(page: Page): Promise<string | null> {
  return page.evaluate(() => sessionStorage.getItem('sora.token'));
}

/**
 * Sign in through the login page with nobody signed in. With a session the
 * page is "Switch account", which is a different flow, so its heading is
 * asserted first.
 */
export async function signInUI(page: Page, username: string, password: string, landing: string): Promise<void> {
  await page.context().setExtraHTTPHeaders({ 'X-Real-IP': freshAddress() });
  await page.goto('/login');
  await expect(page.getByRole('heading', { level: 1, name: 'Sign in', exact: true })).toBeVisible();
  await page.getByLabel('Username', { exact: true }).fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL((url) => url.pathname === landing);
  await expect(page.locator('.shell-user')).toHaveText(`${username} · ${landing === '/history' ? 'specialist' : 'admin'}`);
  expect(await sessionToken(page)).toBeTruthy();
}

/** The header's "Sign out" button. The session ends in place; there is no navigation. */
export async function signOutUI(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.locator('header').getByRole('link', { name: 'Sign in', exact: true })).toBeVisible();
  await expect(page.locator('.shell-user')).toHaveCount(0);
  expect(await sessionToken(page)).toBeNull();
  await page.context().setExtraHTTPHeaders({ 'X-Real-IP': freshAddress() });
}

function field(page: Page, label: RegExp) {
  return page.locator('.ev-field', { has: page.locator('label', { hasText: label }) });
}

export const projectNameInput = (page: Page) => field(page, /^Project name$/).locator('input');

/** Fill the evaluate form. Its labels are not associated with the inputs. */
export async function fillEvaluationForm(page: Page, projectName: string): Promise<void> {
  await projectNameInput(page).fill(projectName);
  await field(page, /^Budget \(USD\)$/).locator('input').fill('100000');
  await field(page, /^CO₂ Reduction \(t\/yr\)$/).locator('input').fill('150');
  await field(page, /^Social Impact \(1–10\)$/).locator('input').fill('7');
  await field(page, /^Duration \(months\)$/).locator('input').fill('24');
  const country = field(page, /^Country$/);
  await country.locator('.cb-trigger').click();
  await country.locator('.cb-search').fill('Germany');
  await country.locator('.cb-item', { hasText: /^Germany$/ }).click();
  await expect(country.locator('.cb-trigger')).toContainText('Germany');
}

export const calculateButton = (page: Page) => page.getByRole('button', { name: 'Calculate', exact: true });
export const saveButton = (page: Page) => page.getByRole('button', { name: 'Calculate and save', exact: true });

/** POST /api/v1/evaluate?save=true */
export function isSaveRequest(req: Request): boolean {
  const u = new URL(req.url());
  return req.method() === 'POST' && u.pathname === `${API}/evaluate` && u.searchParams.get('save') === 'true';
}

/** POST /api/v1/evaluate without save: a calculation (and each What-If sweep point). */
export function isCalculateRequest(req: Request): boolean {
  const u = new URL(req.url());
  return req.method() === 'POST' && u.pathname === `${API}/evaluate` && !u.searchParams.has('save');
}

/** The SAVE_PATTERN for page.route / waitForResponse. */
export const SAVE_URL = (url: URL) => url.pathname === `${API}/evaluate` && url.searchParams.get('save') === 'true';

/** Text of a PDF through a real parser: pypdf, run by $E2E_PYTHON (default python3). */
export function pdfText(bytes: Buffer): string {
  const dir = mkdtempSync(join(tmpdir(), 'sora-e2e-pdf-'));
  try {
    const file = join(dir, 'report.pdf');
    writeFileSync(file, bytes);
    const helper = join(dirname(fileURLToPath(import.meta.url)), '..', 'pdf_text.py');
    return execFileSync(process.env.E2E_PYTHON || 'python3', [helper, file], { encoding: 'utf-8' });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** A saved evaluation created through the UI by the signed-in specialist. */
export async function saveThroughUI(page: Page, projectName: string): Promise<number> {
  await page.goto('/evaluate');
  await fillEvaluationForm(page, projectName);
  const saved = page.waitForResponse((r) => isSaveRequest(r.request()));
  await saveButton(page).click();
  const res = await saved;
  expect(res.status()).toBe(201);
  const body = await res.json();
  expect(body.saved).toBe(true);
  expect(Number.isSafeInteger(body.evaluation_id) && body.evaluation_id > 0).toBe(true);
  await expect(page.locator('.ev-save-ok')).toContainText(`Saved as evaluation #${body.evaluation_id}.`);
  return body.evaluation_id as number;
}
