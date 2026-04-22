import { api } from "../client";
import type { EvaluateRequest, EvaluateResponse, ExplainResponse, CountriesMap } from "../types";
export const evaluateApi = {
  countries: () => api<CountriesMap>("/countries"),
  evaluate: (b:EvaluateRequest) => api<EvaluateResponse>("/evaluate", { method:"POST", body: JSON.stringify(b) }),
  explain:  (b:EvaluateRequest) => api<ExplainResponse>("/predict/explain", { method:"POST", body: JSON.stringify(b) }),
};
