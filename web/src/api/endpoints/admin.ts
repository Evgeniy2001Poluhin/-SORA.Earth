import { api } from "../client";
import type { RetrainLogResponse, FeatureImportanceResponse } from "../types";

export const adminApi = {
  retrainLog: () => api<RetrainLogResponse>("/admin/retrain-log"),
  featureImportance: () => api<FeatureImportanceResponse>("/model/feature-importance"),
};
