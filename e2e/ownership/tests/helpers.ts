import { Page, expect, Response } from '@playwright/test';

export interface AdminToken {
  access_token: string;
}

export interface UserCredentials {
  username: string;
  password: string;
}

/**
 * Login as admin via API and return the access token.
 */
export async function getAdminToken(baseURL: string): Promise<string> {
  const response = await fetch(`${baseURL}/api/v1/auth/login-json`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username: 'admin',
      password: 'sora2026', // dev password from bootstrap --dev
    }),
  });

  if (!response.ok) {
    throw new Error(`Admin login failed: ${response.status}`);
  }

  const data = await response.json() as AdminToken;
  return data.access_token;
}

/**
 * Create a specialist account via admin API.
 */
export async function createSpecialistAccount(
  baseURL: string,
  adminToken: string,
  username: string,
  password: string
): Promise<void> {
  const response = await fetch(`${baseURL}/api/v1/admin/users`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${adminToken}`,
    },
    body: JSON.stringify({ username, password, role: 'specialist' }),
  });

  if (!response.ok && response.status !== 409) { // 409 = already exists
    throw new Error(`Create user failed: ${response.status}`);
  }
}

/**
 * Block a user via admin API.
 */
export async function blockUser(
  baseURL: string,
  adminToken: string,
  userId: string
): Promise<void> {
  const response = await fetch(`${baseURL}/api/v1/admin/users/${userId}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${adminToken}`,
    },
    body: JSON.stringify({ is_blocked: true }),
  });

  if (!response.ok) {
    throw new Error(`Block user failed: ${response.status}`);
  }
}

/**
 * Get user ID by username via admin list.
 */
export async function getUserId(
  baseURL: string,
  adminToken: string,
  username: string
): Promise<string | null> {
  const response = await fetch(`${baseURL}/api/v1/admin/users`, {
    headers: { 'Authorization': `Bearer ${adminToken}` },
  });

  if (!response.ok) {
    throw new Error(`List users failed: ${response.status}`);
  }

  const users = await response.json() as Array<{ user_id: string; username: string }>;
  const user = users.find(u => u.username === username);
  return user?.user_id ?? null;
}

/**
 * Count evaluations via admin API (no ownership filter).
 */
export async function countAllEvaluations(
  baseURL: string,
  adminToken: string
): Promise<number> {
  const response = await fetch(`${baseURL}/api/v1/history?limit=1`, {
    headers: { 'Authorization': `Bearer ${adminToken}` },
  });

  if (!response.ok) {
    throw new Error(`Count evaluations failed: ${response.status}`);
  }

  const data = await response.json() as { total: number };
  return data.total;
}

/**
 * Get evaluation history for the current user.
 */
export async function getHistory(
  baseURL: string,
  token: string
): Promise<Array<{ id: number; name: string }>> {
  const response = await fetch(`${baseURL}/api/v1/history`, {
    headers: { 'Authorization': `Bearer ${token}` },
  });

  if (!response.ok) {
    throw new Error(`Get history failed: ${response.status}`);
  }

  const data = await response.json() as { items: Array<{ id: number; name: string }> };
  return data.items;
}

/**
 * Sign in via the UI (fills the login form and submits).
 */
export async function signInUI(
  page: Page,
  username: string,
  password: string
): Promise<void> {
  await page.goto('/login');

  // Fill credentials
  await page.getByLabel(/username/i).fill(username);
  await page.getByLabel(/password/i).fill(password);

  // Submit and wait for navigation (specialist lands on /history, not /evaluate)
  await Promise.all([
    page.waitForURL((url) => !url.pathname.includes('/login')),
    page.getByRole('button', { name: /sign in/i }).click(),
  ]);
}

/**
 * Sign out via the UI.
 */
export async function signOutUI(page: Page): Promise<void> {
  // Click the "Sign out" button
  await page.getByRole('button', { name: 'Sign out' }).click();

  // Wait for redirect to home
  await page.waitForURL('/');
}

/**
 * Fill the evaluation form with synthetic project data.
 */
export async function fillEvaluationForm(
  page: Page,
  projectName: string
): Promise<void> {
  // Labels not associated with inputs (accessibility issue) - use structural selectors
  await page.locator('.ev-field', { has: page.locator('label', { hasText: /^Project name$/ }) }).locator('input').fill(projectName);
  await page.locator('.ev-field', { has: page.locator('label', { hasText: /^Budget \(USD\)$/ }) }).locator('input').fill('100000');
  await page.locator('.ev-field', { has: page.locator('label', { hasText: /^CO₂ Reduction \(t\/yr\)$/ }) }).locator('input').fill('150');
  await page.locator('.ev-field', { has: page.locator('label', { hasText: /^Social Impact \(1–10\)$/ }) }).locator('input').fill('7');
  await page.locator('.ev-field', { has: page.locator('label', { hasText: /^Duration \(months\)$/ }) }).locator('input').fill('24');

  // Country field
  // A custom combobox: a trigger button, then a search box and clickable items.
  const country = page.locator('.ev-field', { has: page.locator('label', { hasText: /^Country$/ }) });
  await country.locator('.cb-trigger').click();
  await country.locator('.cb-search').fill('Germany');
  await country.locator('.cb-item', { hasText: /^Germany$/ }).click();
  await expect(country.locator('.cb-trigger')).toContainText('Germany');
}

/**
 * Wait for a network response matching a predicate.
 */
export async function waitForResponse(
  page: Page,
  urlPattern: string | RegExp,
  method?: string
): Promise<Response> {
  return page.waitForResponse(
    (resp) => {
      const matchesUrl = typeof urlPattern === 'string'
        ? resp.url().includes(urlPattern)
        : urlPattern.test(resp.url());
      const matchesMethod = !method || resp.request().method() === method;
      return matchesUrl && matchesMethod;
    },
    { timeout: 10000 }
  );
}

/**
 * Assert element contains text (case-insensitive substring).
 */
export async function assertContains(
  page: Page,
  text: string | RegExp,
  options?: { timeout?: number }
): Promise<void> {
  const pattern = typeof text === 'string' ? new RegExp(text, 'i') : text;
  await expect(page.getByText(pattern)).toBeVisible(options);
}

/**
 * Assert element does NOT contain text.
 */
export async function assertNotContains(
  page: Page,
  text: string | RegExp,
  options?: { timeout?: number }
): Promise<void> {
  const pattern = typeof text === 'string' ? new RegExp(text, 'i') : text;
  await expect(page.getByText(pattern)).not.toBeVisible(options);
}
