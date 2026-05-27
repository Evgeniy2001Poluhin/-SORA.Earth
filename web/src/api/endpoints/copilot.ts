import { api } from "../client";

export interface CopilotShapValue {
  feature: string;
  shap_value: number;
}

export interface CopilotRequest {
  probability: number;
  features: Record<string, number>;
  shap_values?: CopilotShapValue[];
  project?: Record<string, any>;
  model_version?: string;
}

export interface CopilotDriver {
  feature: string;
  feature_label: string;
  shap_value?: number;
  direction?: string;
  note?: string;
}

export interface CopilotSource {
  id: string;
  title: string;
  category: string;
  score: number;
  excerpt: string;
}

export interface CopilotResponse {
  verdict: { label: string; level: string };
  probability: number;
  confidence: string;
  key_drivers: { positive: CopilotDriver[]; negative: CopilotDriver[] };
  risks: string[];
  recommendation: string;
  model_version: string;
  explanation_mode: string;
  executive_summary?: string;
  sources?: CopilotSource[];
  rag_query?: string;
}

export interface CopilotHealth {
  ok: boolean;
  llm_enabled: boolean;
  explanation_mode: string;
  supported_features: string[];
}

export const copilotApi = {
  explain: (b: CopilotRequest) =>
    api<CopilotResponse>("/copilot/explain", { method: "POST", body: JSON.stringify(b) }),
  health: () => api<CopilotHealth>("/copilot/health"),
};
