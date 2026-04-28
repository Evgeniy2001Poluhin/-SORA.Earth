import { api } from "../client";
import type {
  DriftBaselineStatus,
  DriftBaselineFitResponse,
  DriftSimulateResponse,
} from "../types";

export const driftBaselineApi = {
  // GET /api/v1/mlops/drift/baseline
  status: () => api<DriftBaselineStatus>("/mlops/drift/baseline"),

  // POST /api/v1/mlops/drift/baseline/fit?csv_path=...
  fit: (csv_path: string = "data/projects.csv") =>
    api<DriftBaselineFitResponse>(
      `/mlops/drift/baseline/fit?csv_path=${encodeURIComponent(csv_path)}`,
      { method: "POST" }
    ),

  // DELETE /api/v1/mlops/drift/baseline
  remove: () =>
    api<{ status: string }>("/mlops/drift/baseline", { method: "DELETE" }),

  // POST /api/v1/mlops/drift/simulate?mode=...&n=...&shift=...
  simulate: (
    mode: "stable" | "drift" | "custom",
    n: number = 50,
    shift?: number
  ) => {
    const q = new URLSearchParams({ mode, n: String(n) });
    if (shift !== undefined) q.set("shift", String(shift));
    return api<DriftSimulateResponse>(`/mlops/drift/simulate?${q.toString()}`, {
      method: "POST",
    });
  },
};
