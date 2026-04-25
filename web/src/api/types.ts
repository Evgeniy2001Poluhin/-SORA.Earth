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
