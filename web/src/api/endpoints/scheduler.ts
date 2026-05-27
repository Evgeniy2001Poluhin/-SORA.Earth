import { api } from "../client";

export interface SchedulerStatus {
  running: boolean;
  enabled: boolean;
  jobs: unknown[];
  retrain_history_count: number;
}

export interface RetrainHistoryItem {
  id: number;
  started_at: string;
  finished_at: string | null;
  duration_sec: number | null;
  status: string;
  trigger_source: string;
  job_name: string;
  model_version: string | null;
  data_version: string | null;
  message: string | null;
  error_message: string | null;
  metrics: Record<string, number> | null;
}

export const schedulerApi = {
  status: () => api<SchedulerStatus>("/scheduler/status"),
  history: () => api<RetrainHistoryItem[]>("/scheduler/retrain/history"),
  trigger: () =>
    api<unknown>("/scheduler/retrain/trigger", { method: "POST" }),
  refreshExternal: () =>
    api<unknown>("/scheduler/refresh_external", { method: "POST" }),
};
