import { api } from "../client";
import type {
  DriftBaselineStatus,
  DriftBaselineFitResponse,
} from "../types";

export const driftBaselineApi = {
  status: () => api<DriftBaselineStatus>("/mlops/drift/baseline"),
  fit: (n_samples = 200) =>
    api<DriftBaselineFitResponse>(`/mlops/drift/baseline/fit?n_samples=${n_samples}`, {
      method: "POST",
    }),
  remove: () =>
    api<{ status: string }>("/mlops/drift/baseline", { method: "DELETE" }),
  simulate: (mode: "stable" | "drift", n = 50) =>
    api<{ status: string; n: number }>(
      `/mlops/drift/simulate?mode=${mode}&n=${n}`,
      { method: "POST" }
    ),
};
