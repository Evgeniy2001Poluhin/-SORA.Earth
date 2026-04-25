import { api } from "../client";
import type {
  DriftBaselineStatus,
  DriftBaselineFitResponse,
} from "../types";

export const driftBaselineApi = {
  status: () => api<DriftBaselineStatus>("/drift/baseline/status"),
  fit: (n_samples = 200) =>
    api<DriftBaselineFitResponse>(`/drift/baseline/fit?n_samples=${n_samples}`, {
      method: "POST",
    }),
  remove: () =>
    api<{ status: string }>("/drift/baseline/delete", { method: "DELETE" }),
  simulate: (mode: "stable" | "drift", n = 50) =>
    api<{ status: string; n: number }>(
      `/drift/simulate?mode=${mode}&n=${n}`,
      { method: "POST" }
    ),
};
