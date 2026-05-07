import { api } from "../client";
import type {
  MlopsHealth,
  ModelStatus,
  ModelMetrics,
} from "../types";

export const mlopsApi = {
  health:       () => api<MlopsHealth>("/mlops/health"),
  modelStatus:  () => api<ModelStatus>("/model/status"),
  modelMetrics: () => api<ModelMetrics>("/model/metrics"),
};
