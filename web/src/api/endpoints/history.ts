import { api, apiBlob } from "../client";
import { isMock } from "../mock";

/**
 * One evaluation as /history shows it (app/schemas.py EvaluationView). Only
 * `id` is guaranteed; every other column is nullable and is rendered as
 * unknown, never as a default. `owner_user_id` and `data_scope` appear only on
 * an administrator's view.
 */
export type HistoryItem = {
  id: number;
  name: string | null;
  budget: number | null;
  co2_reduction: number | null;
  social_impact: number | null;
  duration_months: number | null;
  total_score: number | null;
  environment_score: number | null;
  social_score: number | null;
  economic_score: number | null;
  success_probability: number | null;
  recommendation: string | null;
  risk_level: string | null;
  region: string | null;
  lat: number | null;
  lon: number | null;
  created_at: string | null;
  owner_user_id?: string | null;
  data_scope?: string | null;
};
export type HistoryPage = { items: HistoryItem[]; total: number; limit: number; offset: number };
export type HistoryParams = { region?: string; risk_level?: "LOW" | "MED" | "HIGH"; date_from?: string; date_to?: string; min_score?: number; max_score?: number; limit?: number; offset?: number };

const delay = <T,>(v: T, ms = 250) => new Promise<T>((r) => setTimeout(() => r(v), ms));
const REGIONS = ["Europe", "North America", "Asia", "South America", "Africa", "Oceania"];
const ITEMS: HistoryItem[] = Array.from({ length: 546 }, (_, i) => {
  const score = 45 + Math.random() * 45; const rl = score > 75 ? "LOW" : score > 60 ? "MED" : "HIGH";
  const d = new Date(Date.now() - i * 86400000 * 0.5);
  return { id: i + 1, name: ["Solar Farm", "Wind", "Reforest", "Water", "EV"][i % 5],
    budget: 80000 + Math.floor(Math.random() * 400000), co2_reduction: 60 + Math.floor(Math.random() * 300),
    social_impact: 5 + Math.floor(Math.random() * 5), duration_months: 12 + Math.floor(Math.random() * 36),
    total_score: +score.toFixed(1), environment_score: +(score + Math.random() * 8 - 4).toFixed(1),
    social_score: +(score + Math.random() * 10 - 5).toFixed(1), economic_score: +(score + Math.random() * 8 - 4).toFixed(1),
    success_probability: +(60 + score * 0.35).toFixed(1), recommendation: null, risk_level: rl,
    region: REGIONS[i % 6], lat: 50 + Math.random() * 15, lon: Math.random() * 30 - 5, created_at: d.toISOString() };
});

/** Reads of project rows are private: never from, or into, the HTTP cache. */
const PRIVATE: RequestInit = { cache: "no-store" };

export const historyApi = {
  list(p: HistoryParams = {}, opts: { signal?: AbortSignal } = {}) {
    if (!isMock) {
      const q = new URLSearchParams();
      Object.entries(p).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") q.set(k, String(v)); });
      return api<HistoryPage>("/history" + (q.toString() ? "?" + q : ""), { ...PRIVATE, signal: opts.signal });
    }
    let f = ITEMS.slice();
    if (p.region) f = f.filter((x) => x.region === p.region);
    if (p.risk_level) f = f.filter((x) => x.risk_level === p.risk_level);
    if (p.min_score) f = f.filter((x) => (x.total_score ?? -1) >= p.min_score!);
    if (p.max_score) f = f.filter((x) => (x.total_score ?? 101) <= p.max_score!);
    const off = p.offset || 0, lim = p.limit || 20;
    return delay({ items: f.slice(off, off + lim), total: f.length, limit: lim, offset: off });
  },
  getById: (id: number, opts: { signal?: AbortSignal } = {}): Promise<HistoryItem> => {
    if (!isMock) return api<HistoryItem>("/history/" + id, { ...PRIVATE, signal: opts.signal });
    const found = ITEMS.find((x) => x.id === id);
    return found ? delay(found) : Promise.reject(new Error("API 404: Evaluation not found"));
  },
  /** Every evaluation the caller may read, whatever the page's filters (app/api/evaluate.py export_csv). */
  exportCsv: (opts: { signal?: AbortSignal } = {}) =>
    isMock
      ? delay(new Blob(["ID,Name\n"], { type: "text/csv" }))
      : apiBlob("/export/csv", { ...PRIVATE, signal: opts.signal }),
  remove: (id: number) => isMock ? delay({ status: "deleted" }) : api<{ status: string }>("/history/" + id, { method: "DELETE" }),
  clear: () => isMock ? delay({ status: "cleared" }) : api<{ status: string }>("/history", { method: "DELETE" }),
};
