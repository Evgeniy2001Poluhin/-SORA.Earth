import { api } from "../client";

export interface SchedulerStatus {
  running: boolean;
  /** Absent when the scheduler container could not be reached: the field
   *  describes that container, and this process cannot speak for it. */
  enabled?: boolean | null;
  jobs: unknown[];
  /** Null when the count could not be read. Not 0 -- a number nobody could
   *  read is not zero, and the panel must not print one. */
  retrain_history_count: number | null;
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
