import { api } from "../client";
import type {
  DriftBaselineStatus,
  DriftBaselineFitResponse,
  DriftSimulateResponse,
} from "../types";

export type MlflowDriftEvent = {
  run_id: string;
  start_time: string;
  experiment_id?: string;
  "metrics.drift_score"?: number;
  "metrics.drifted_features_count"?: number;
  "metrics.n_samples_ref"?: number;
  "metrics.n_samples_cur"?: number;
  "tags.baseline_id"?: string;
  "params.drifted_features"?: string;
};
export type MlflowHistoryResp = { events: MlflowDriftEvent[]; count: number };

export type KsFeatureStat = { ks_stat: number; p_value: number; drift: boolean };
export type ModelDriftKsResp = {
  status: string;
  drift_detected: boolean;
  window: number;
  features: Record<string, KsFeatureStat>;
};

export const driftBaselineApi = {
  status: () => api<DriftBaselineStatus>("/mlops/drift/baseline"),
  fit: (csv_path: string = "data/projects.csv") =>
    api<DriftBaselineFitResponse>(
      `/mlops/drift/baseline/fit?csv_path=${encodeURIComponent(csv_path)}`,
      { method: "POST" }
    ),
  remove: () => api<{ status: string }>("/mlops/drift/baseline", { method: "DELETE" }),
  simulate: (mode: "stable" | "drift" | "custom", n: number = 50, shift?: number) => {
    const q = new URLSearchParams({ mode, n: String(n) });
    if (shift !== undefined) q.set("shift", String(shift));
    return api<DriftSimulateResponse>(`/mlops/drift/simulate?${q.toString()}`, { method: "POST" });
  },
  mlflowHistory: () => api<MlflowHistoryResp>("/model/drift/mlflow-history"),
  ksReport: () => api<ModelDriftKsResp>("/model/drift"),
};
