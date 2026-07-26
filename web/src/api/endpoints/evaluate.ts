import { api } from "../client";
import type { EvaluateRequest, EvaluateResponse, ExplainResponse, CountriesMap } from "../types";
import { mockEvaluate, isMock } from "../mock";

const delay = <T,>(v: T, ms = 350) => new Promise<T>(r => setTimeout(() => r(v), ms));

const COUNTRIES: CountriesMap = {
  Sweden:{region:"Europe"}, Germany:{region:"Europe"}, France:{region:"Europe"},
  Spain:{region:"Europe"}, Norway:{region:"Europe"}, Finland:{region:"Europe"},
  Canada:{region:"North America"}, USA:{region:"North America"},
  Brazil:{region:"South America"}, India:{region:"Asia"}, Japan:{region:"Asia"},
  Kenya:{region:"Africa"}, Australia:{region:"Oceania"},
} as any;

const mockExplain = (b: EvaluateRequest): ExplainResponse => {
  const r = mockEvaluate(b);
  return { ...r, shap_values: (r as any).shap_values, base_value: 50, prediction: r.total_score } as any;
};

const mockRanking = (b: EvaluateRequest) => {
  const r = mockEvaluate(b);
  return { rank: Math.ceil((100 - r.total_score) / 4), percentile: +r.total_score.toFixed(0), peers: 250 };
};

const mockMonteCarlo = (b: any) => {
  const r = mockEvaluate(b);
  const samples = Array.from({length: 1000}, () => r.total_score + (Math.random()-0.5)*12);
  return { mean: r.total_score, std: 4.2, p5: r.total_score-7, p50: r.total_score, p95: r.total_score+7, samples: samples.slice(0,200) };
};

const mockWhatIf = () => ({ scenarios: [
  { label: "+20% budget", delta_score: 2.4 },
  { label: "+50% CO₂",    delta_score: 5.1 },
  { label: "−6 months",   delta_score: -1.8 },
]});

export const evaluateApi = {
  countries: () => isMock ? delay(COUNTRIES, 100) : api<CountriesMap>("/countries"),
  evaluate: (b: EvaluateRequest) => isMock ? delay(mockEvaluate(b)) : api<EvaluateResponse>("/evaluate", { method:"POST", body: JSON.stringify(b) }),
  explain:  (b: EvaluateRequest) => isMock ? delay(mockExplain(b)) : api<ExplainResponse>("/predict/explain", { method:"POST", body: JSON.stringify(b) }),
  ranking:  (b: EvaluateRequest) => isMock ? delay(mockRanking(b)) : api<any>("/evaluate/ranking", { method:"POST", body: JSON.stringify(b) }),
  monteCarlo: (b: any) => isMock ? delay(mockMonteCarlo(b)) : api<any>("/evaluate/monte-carlo", { method:"POST", body: JSON.stringify(b) }),
  whatIf: (b: any) => isMock ? delay(mockWhatIf()) : api<any>("/what-if", { method:"POST", body: JSON.stringify(b) }),
};
