import { api } from "../client";
import type { EvaluateRequest, EvaluateResponse, ExplainResponse, CountriesMap, ShapFeature, EvaluateProjectRequest,
  MonteCarloResponse, EvaluateRankingResponse, EvaluateRankingEntry, WhatIfResponse, WhatIfVariation, WhatIfRequest } from "../types";
import { mockEvaluate, isMock } from "../mock";

const delay = <T,>(v: T, ms = 350) => new Promise<T>(r => setTimeout(() => r(v), ms));

const COUNTRIES: CountriesMap = {
  Sweden:"Europe", Germany:"Europe", France:"Europe",
  Spain:"Europe", Norway:"Europe", Finland:"Europe",
  Canada:"North America", USA:"North America",
  Brazil:"South America", India:"Asia", Japan:"Asia",
  Kenya:"Africa", Australia:"Oceania",
};

const mockExplain = (b: EvaluateRequest): ExplainResponse => {
  const r = mockEvaluate(b);
  const feats: ShapFeature[] = [
    { feature:"co2_reduction", value:b.co2_reduction_tons_per_year, shap_value:8.4, direction:"positive", impact:"high" },
    { feature:"budget", value:b.budget_usd, shap_value:5.1, direction:"positive", impact:"medium" },
    { feature:"social_impact", value:b.social_impact_score, shap_value:4.8, direction:"positive", impact:"medium" },
    { feature:"duration_months", value:b.project_duration_months, shap_value:-2.3, direction:"negative", impact:"low" },
  ];
  return {
    prediction: r.total_score >= 60 ? 1 : 0,
    probability: r.success_probability,
    base_value: 50,
    explanation: feats,
    all_features: feats.map(f => ({ name:f.feature, direction:f.direction, impact:f.impact, shap_value:f.shap_value, value:f.value })),
  };
};

const mockRanking = (b: EvaluateProjectRequest): EvaluateRankingResponse => {
  const r = mockEvaluate(b);
  const ranking: EvaluateRankingEntry[] = Object.entries(COUNTRIES).map(([country, region], i) => ({
    country, region,
    total_score: +(r.total_score - i * 1.4).toFixed(2),
    environment_score: r.environment_score,
    social_score: r.social_score,
    economic_score: r.economic_score,
    success_probability: r.success_probability,
    risk_level: r.risk_level,
  })).sort((a, b2) => (b2.total_score ?? 0) - (a.total_score ?? 0));
  return { count: ranking.length, ranking };
};

const mockMonteCarlo = (b: EvaluateProjectRequest): MonteCarloResponse => {
  const r = mockEvaluate(b);
  const scores = Array.from({length: 1000}, () => r.total_score + (Math.random()-0.5)*12).sort((x, y) => x - y);
  const pct = (p: number) => +scores[Math.min(scores.length - 1, Math.floor((scores.length - 1) * p / 100))].toFixed(2);
  const lo = scores[0], hi = scores[scores.length - 1];
  const nbins = 20, width = Math.max((hi - lo) / nbins, 0.01);
  const counts = new Array(nbins).fill(0);
  for (const s of scores) counts[Math.min(Math.floor((s - lo) / width), nbins - 1)]++;
  return {
    n: scores.length, mean: +r.total_score.toFixed(2), stdev: 4.2,
    min: +lo.toFixed(2), max: +hi.toFixed(2),
    p10: pct(10), p50: pct(50), p90: pct(90),
    histogram: { edges: Array.from({length: nbins + 1}, (_, i) => +(lo + i * width).toFixed(2)), counts },
  };
};

const mockWhatIf = (b: WhatIfRequest): WhatIfResponse => {
  const duration = b.duration_months ?? b.project_duration_months ?? 18;
  const base = mockEvaluate({
    project_name: b.project_name ?? "Project",
    country: b.region ?? b.country ?? "Sweden",
    budget_usd: b.budget,
    co2_reduction_tons_per_year: b.co2_reduction,
    social_impact_score: b.social_impact,
    project_duration_months: duration,
  });
  const vary = (new_value: number, score_change: number, prob_change: number): WhatIfVariation => ({
    new_value: Math.round(new_value),
    new_score: +(base.total_score + score_change).toFixed(2),
    score_change,
    new_probability: +(base.success_probability + prob_change).toFixed(2),
    prob_change,
  });
  return { base, variations: {
    budget: vary(b.budget * 1.2, 2.4, 0.03),
    co2_reduction: vary(b.co2_reduction * 1.5, 5.1, 0.07),
    social_impact: vary(b.social_impact + 2, 3.2, 0.04),
    duration_months: vary(Math.max(duration - 6, 1), -1.8, -0.02),
  }};
};

export const evaluateApi = {
  countries: () => isMock ? delay(COUNTRIES, 100) : api<CountriesMap>("/countries"),
  evaluate: (b: EvaluateRequest) => isMock ? delay(mockEvaluate(b)) : api<EvaluateResponse>("/evaluate", { method:"POST", body: JSON.stringify(b) }),
  explain:  (b: EvaluateRequest) => isMock ? delay(mockExplain(b)) : api<ExplainResponse>("/predict/explain", { method:"POST", body: JSON.stringify(b) }),
  ranking:  (b: EvaluateProjectRequest) => isMock ? delay(mockRanking(b)) : api<EvaluateRankingResponse>("/evaluate/ranking", { method:"POST", body: JSON.stringify(b) }),
  monteCarlo: (b: EvaluateProjectRequest & { n?: number }) => isMock ? delay(mockMonteCarlo(b)) : api<MonteCarloResponse>("/evaluate/monte-carlo", { method:"POST", body: JSON.stringify(b) }),
  whatIf: (b: WhatIfRequest) => isMock ? delay(mockWhatIf(b)) : api<WhatIfResponse>("/what-if", { method:"POST", body: JSON.stringify(b) }),
};
