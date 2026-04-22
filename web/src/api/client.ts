const BASE = "/api/v1";
let token: string | null = null;
export const auth = { set(t:string|null){token=t}, get(){return token} };
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, { ...init,
    headers: { "Content-Type":"application/json",
      ...(token ? { Authorization:`Bearer ${token}` } : {}),
      ...(init.headers || {}) } });
  if (!res.ok) { const t = await res.text().catch(()=>""); throw new Error(`API ${res.status}: ${t||res.statusText}`); }
  return res.json() as Promise<T>;
}
