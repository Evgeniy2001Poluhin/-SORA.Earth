const BASE = "/api/v1";
let token: string | null = null;
let apiKey: string | null = null;

export const auth = {
  set(t: string | null) { token = t; },
  get() { return token; },
  setApiKey(k: string | null) { apiKey = k; },
  getApiKey() { return apiKey; },
};

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string> | undefined) || {}),
  };
  if (token) headers["Authorization"] = "Bearer " + token;
  if (apiKey) headers["X-API-Key"] = apiKey;

  const res = await fetch(BASE + path, { ...init, headers });
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error("API " + res.status + ": " + (t || res.statusText));
  }
  return res.json() as Promise<T>;
}