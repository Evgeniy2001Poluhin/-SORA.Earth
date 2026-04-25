import { api } from "../client";
import type { EvaluateRequest, EvaluateResponse, ExplainResponse, CountriesMap } from "../types";
export const evaluateApi = {
  countries: () => api<CountriesMap>("/countries"),
  evaluate: (b:EvaluateRequest) => api<EvaluateResponse>("/evaluate", { method:"POST", body: JSON.stringify(b) }),
  explain:  (b:EvaluateRequest) => api<ExplainResponse>("/predict/explain", { method:"POST", body: JSON.stringify(b) }),
  ranking:  (b:EvaluateRequest) => api<any>("/evaluate/ranking", { method:"POST", body: JSON.stringify(b) }),
  monteCarlo: (b:any) => api<any>("/evaluate/monte-carlo", { method:"POST", body: JSON.stringify(b) }),
};
