import { api } from "../client";

export type HistoryItem = {
  id: number;
  name: string | null;
  budget: number;
  co2_reduction: number;
  social_impact: number;
  duration_months: number;
  total_score: number;
  environment_score: number;
  social_score: number;
  economic_score: number;
  success_probability: number;
  recommendation: string | null;
  risk_level: string;
  region: string;
  lat: number;
  lon: number;
  created_at: string;
};

export type HistoryPage = {
  items: HistoryItem[];
  total: number;
  limit: number;
  offset: number;
};

export type HistoryParams = {
  region?: string;
  risk_level?: "LOW" | "MED" | "HIGH";
  date_from?: string;
  date_to?: string;
  min_score?: number;
  max_score?: number;
  limit?: number;
  offset?: number;
};

export const historyApi = {
  list(p: HistoryParams = {}) {
    const q = new URLSearchParams();
    Object.entries(p).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    });
    const qs = q.toString();
    return api<HistoryPage>("/history" + (qs ? "?" + qs : ""));
  },
  getById(id: number) {
    return api<HistoryItem>("/history/" + id);
  },
  remove(id: number) {
    return api<{ status: string }>("/history/" + id, { method: "DELETE" });
  },
  clear() {
    return api<{ status: string }>("/history", { method: "DELETE" });
  },
};
