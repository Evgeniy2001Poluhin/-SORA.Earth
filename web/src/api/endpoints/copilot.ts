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

export interface CopilotPiiFinding { type: string; text: string; start: number; end: number; confidence: number; }
export interface CopilotBiasFinding { pattern: string; match: string; note: string; }
export interface CopilotCompliance {
  passed: boolean;
  pii_findings: CopilotPiiFinding[];
  bias_findings: CopilotBiasFinding[];
  policy_violations: CopilotBiasFinding[];
  redacted_text: string;
  risk_score: number;
  engine: string;
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
  compliance?: CopilotCompliance;
}

export type CopilotStreamEvent =
  | { type: "meta"; probability: number; rag_query: string }
  | { type: "section"; name: "executive_summary" | "recommendation" }
  | { type: "token"; value: string }
  | { type: "done"; sources: CopilotSource[]; compliance: CopilotCompliance };

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

  async *explainStream(b: CopilotRequest): AsyncGenerator<CopilotStreamEvent, void, unknown> {
    const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api/v1";
    const res = await fetch(base + "/copilot/explain/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(b),
    });
    if (!res.ok || !res.body) {
      throw new Error("Stream failed: HTTP " + res.status);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const chunks = buf.split("\n\n");
      buf = chunks.pop() ?? "";
      for (const chunk of chunks) {
        const line = chunk.trim();
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try {
          yield JSON.parse(payload) as CopilotStreamEvent;
        } catch (err) {
          console.warn("SSE parse error:", err, payload);
        }
      }
    }
  },
};
