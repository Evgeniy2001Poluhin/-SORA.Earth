import { api } from "../client";
import type {
  DiscrepancyResponse,
  UncertaintyResponse,
  ExplainLocalRequest,
} from "../types";

export const calibrationApi = {
  // Cross-model discrepancy: rf_v1 vs stacking_v2 vs calibrated_v2
  discrepancy: (b: ExplainLocalRequest) =>
    api<DiscrepancyResponse>("/calibration/discrepancy", {
      method: "POST",
      body: JSON.stringify(b),
    }),

  // Tree-level uncertainty (90% CI from RF estimators)
  uncertainty: (b: ExplainLocalRequest) =>
    api<UncertaintyResponse>("/predict/uncertainty", {
      method: "POST",
      body: JSON.stringify(b),
    }),
};
