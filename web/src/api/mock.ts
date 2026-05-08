import type { EvaluateRequest, EvaluateResponse } from "./types";

const seed = (s: string) => { let h=2166136261; for(const c of s) h=Math.imul(h^c.charCodeAt(0),16777619); return ()=>((h=Math.imul(h^(h>>>15),2246822507))>>>0)/4294967296; };

export function mockEvaluate(req: EvaluateRequest): EvaluateResponse {
  const r = seed(req.project_name + req.country + req.budget_usd);
  const env = Math.min(100, 40 + (req.co2_reduction_tons_per_year/10) + r()*15);
  const soc = Math.min(100, req.social_impact_score*7 + r()*20);
  const eco = Math.min(100, 50 + (req.budget_usd/8000) + r()*10);
  const total = (env*0.45 + soc*0.30 + eco*0.25);
  const risk = total >= 75 ? "Low" : total >= 55 ? "Medium" : "High";
  return {
    project_name: req.project_name, country: req.country,
    total_score: +total.toFixed(1),
    environment_score: +env.toFixed(1),
    social_score: +soc.toFixed(1),
    economic_score: +eco.toFixed(1),
    risk_level: risk,
    success_probability: +(60 + total*0.35).toFixed(1),
    shap_values: { budget_usd: r()*0.3-0.15, co2_reduction: r()*0.4, social_impact: r()*0.25, duration: r()*0.2-0.1 },
  } as any;
}

export const isMock = import.meta.env.VITE_USE_MOCK === "1" || !import.meta.env.VITE_API_BASE;
