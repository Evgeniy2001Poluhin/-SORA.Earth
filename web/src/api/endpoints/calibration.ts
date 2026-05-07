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


// ============================================================================
// Brier + Reliability (added Sprint 7 — thesis pillar #2)
// ============================================================================
export interface CalibrationDataset {
  probs: number[];
  labels: number[];
  n_bins?: number;
}
export interface BrierResult {
  brier: number;
  ece: number;
  n_samples: number;
  n_bins: number;
}
export interface ReliabilityResult {
  n_samples: number;
  n_bins: number;
  base_rate: number;
  brier: number;
  ece: number;
  curve: {
    bin_lower: number[];
    bin_upper: number[];
    mean_predicted: (number | null)[];
    mean_observed: (number | null)[];
    count: number[];
  };
  murphy: {
    reliability: number;
    resolution: number;
    uncertainty: number;
  };
}

export const calibrationQualityApi = {
  brier: (d: CalibrationDataset) =>
    api<BrierResult>("/calibration/brier", {
      method: "POST",
      body: JSON.stringify(d),
    }),
  reliability: (d: CalibrationDataset) =>
    api<ReliabilityResult>("/calibration/reliability", {
      method: "POST",
      body: JSON.stringify(d),
    }),
};
