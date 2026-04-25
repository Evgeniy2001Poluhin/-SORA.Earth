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
