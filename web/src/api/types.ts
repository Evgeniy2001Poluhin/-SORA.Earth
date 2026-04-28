export type RiskLevel = "Low" | "Medium" | "High";
export interface EvaluateRequest {
  project_name: string; country: string; budget_usd: number;
  co2_reduction_tons_per_year: number; social_impact_score: number; project_duration_months: number;
}
export interface EvaluateResponse {
  total_score: number; environment_score: number; social_score: number; economic_score: number;
  success_probability: number; success_probability_v2: number;
  recommendations: string[]; risk_level: RiskLevel;
  esg_weights: { environment: number; social: number; economic: number };
  region: string; lat: number; lon: number;
  country_benchmark: { country: string; co2_per_capita: number; renewable_share: number;
    esg_rank: number; hdi: number;
    project_vs_country: { esg_score_diff: number; above_average: boolean } };
}
export interface ShapFeature {
  feature: string; value: number; shap_value: number;
  direction: "positive"|"negative"; impact: "high"|"medium"|"low";
}
export interface ExplainResponse {
  prediction: 0|1; probability: number; base_value: number;
  explanation: ShapFeature[];
  all_features: { name:string; direction:string; impact:string; shap_value:number; value:number }[];
}
export type CountriesMap = Record<string, string>;

export type RankingItem = {
  country: string;
  co2_per_capita: number;
  renewable_share: number;
  esg_rank: number;
  hdi: number;
  gdp_per_capita: number;
  gini_index: number;
  gov_effectiveness: number;
};
export type RankingResponse = {
  total: number;
  limit: number;
  offset: number;
  data: RankingItem[];
};

// ============ NEW: discrepancy / uncertainty / explain-waterfall / drift-baseline ============

export type RecommendationLevel = "consensus" | "moderate_disagreement" | "high_disagreement";

export interface ModelProba { proba: number; weight: number }

export interface DiscrepancyResponse {
  models: {
    rf_v1: ModelProba;
    stacking_v2: ModelProba;
    calibrated_v2: ModelProba;
  };
  consensus: { weighted_proba: number; method: string };
  divergence: { max_spread: number; std: number; max_pair: [string, string] };
  tree_uncertainty: { std: number; ci_90: [number, number]; n_trees: number };
  recommendation: RecommendationLevel;
}

export interface UncertaintyResponse {
  prediction: { mean: number; median: number; lower_90: number; upper_90: number };
  tree_distribution: { std: number; n_trees: number; min: number; max: number };
  confidence: "high" | "medium" | "low";
}

export interface DriftBaselineStatus {
  exists: boolean;
  fitted_at?: string;
  n_samples?: number;
  feature_count?: number;
}

export interface DriftBaselineFitResponse {
  status: "fitted" | "skipped";
  n_samples: number;
  features: string[];
}

export interface ExplainLocalRequest {
  budget: number;
  co2_reduction: number;
  social_impact: number;
  duration_months: number;
}

export interface ExplainLocalContribution {
  feature: string;
  value: number;
  shap_value: number;
}

export interface ExplainLocalResponse {
  prediction: number;
  base_value: number;
  top_contributions: ExplainLocalContribution[];
}


// === drift v2 contract ===
// Extended baseline status returned by GET /api/v1/mlops/drift/baseline
export interface DriftBaselineStatusV2 extends DriftBaselineStatus {
  fitted?: boolean;
  n_features?: number;
  observations?: number;
  baseline_keys?: string[];
}

// Response of POST /api/v1/mlops/drift/simulate
export interface DriftSimulateResponse {
  status: "simulated";
  mode: "stable" | "drift" | "custom";
  shift_sigma: number;
  shifts: Record<string, number>;
  observations: number;
}


// === mlops control room ===
export interface MlopsHealth {
  model_status: "healthy" | "degraded" | "down" | string;
  drift_status: "stable" | "drift_detected" | "no_baseline" | "insufficient_data" | string;
  observations_tracked: number;
  monitoring: { prometheus?: string; mlflow?: string; drift?: string };
}

export interface ModelMeta {
  retrained_at: string;
  algorithm: string;
  n_estimators: number;
  max_depth: number;
  features: string[];
  total_samples: number;
}

export interface RetrainMetrics {
  accuracy: number;
  f1_score: number;
  best_f1: number;
  roc_auc: number;
  best_threshold: number;
  train_samples: number;
  test_samples: number;
  enrichment_from_log?: number;
}

export interface RetrainHistoryEntry {
  status: "success" | "failed" | string;
  trigger_source: string;
  started_at: string;
  finished_at: string;
  duration_sec: number;
  model_version: string;
  metrics?: RetrainMetrics;
}

export interface ModelStatus {
  current_threshold: number;
  meta: ModelMeta;
  retrain_history: RetrainHistoryEntry[];
}

export interface ModelMetrics {
  metrics: RetrainMetrics;
  meta: ModelMeta;
  models_available: string[];
}


// === auth + admin contracts ===
export interface LoginRequest { username: string; password: string; }
export interface Token { access_token: string; refresh_token: string; token_type: string; expires_in: number; }
export interface UserInfo { username: string; role: "admin" | "analyst" | "viewer" | string; }

export interface RetrainLogItem {
  id: number;
  started_at: string;
  finished_at: string;
  duration_sec: number;
  status: "success" | "failed" | string;
  trigger_source: string;
  job_name: string;
  model_version: string | null;
  data_version: string | null;
  message: string | null;
  error_message: string | null;
  metrics_json: string | null;
}
export interface RetrainLogResponse { items: RetrainLogItem[]; }

export interface FeatureImportanceEntry { name: string; importance: number; }
export interface FeatureImportanceResponse { features: FeatureImportanceEntry[]; }
