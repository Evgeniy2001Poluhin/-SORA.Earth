import { api } from '../client';
import type { ExplainResponse } from '../types';

export interface CopilotShapValue { feature: string; shap_value: number; }
/** The project inputs `/predict/explain` takes (app/schemas.py ProjectInput, by alias).
 *  Narrower than EvaluateRequest on purpose: that type also demands a country and a
 *  project name, which the copilot form does not have, and this page does not invent them. */
export interface ShapInputs { budget_usd: number; co2_reduction_tons_per_year: number; social_impact_score: number; project_duration_months: number; }
export interface CopilotRequest { probability: number; features: Record<string, number>; shap_values?: CopilotShapValue[]; project?: Record<string, unknown>; model_version?: string; session_id?: string; }
export interface CopilotDriver { feature: string; feature_label: string; shap_value?: number; direction?: string; note?: string; }
export interface CopilotSource { id: string; title: string; category: string; score: number; excerpt: string; }
export interface CopilotPiiFinding { type: string; text: string; start: number; end: number; confidence: number; }
export interface CopilotBiasFinding { pattern: string; match: string; note: string; }
export interface CopilotCompliance { passed: boolean; pii_findings: CopilotPiiFinding[]; bias_findings: CopilotBiasFinding[]; policy_violations: CopilotBiasFinding[]; redacted_text: string; risk_score: number; engine: string; }
export interface CopilotResponse { verdict: { label: string; level: string }; probability: number; confidence: string; key_drivers: { positive: CopilotDriver[]; negative: CopilotDriver[] }; risks: string[]; recommendation: string; model_version: string; explanation_mode: string; executive_summary?: string; sources?: CopilotSource[]; rag_query?: string; compliance?: CopilotCompliance; session_id?: string; }
export type CopilotStreamEvent =
  | { type: 'meta'; probability: number; rag_query: string; session_id?: string }
  | { type: 'section'; name: 'executive_summary' | 'recommendation' }
  | { type: 'token'; value: string }
  | { type: 'done'; sources: CopilotSource[]; compliance: CopilotCompliance };
export interface CopilotHealth { ok: boolean; llm_enabled: boolean; explanation_mode: string; supported_features: string[]; }
export interface CopilotSessionSummary { id: string; title: string; created_at: number; updated_at: number; }
export interface CopilotSessionMessage { id: string; role: 'user' | 'assistant'; content: string; citations: string[]; created_at: number; }
export interface CopilotSessionFull extends CopilotSessionSummary { messages: CopilotSessionMessage[]; }
/** The SHAP values a copilot request carries, read from a `/predict/explain` answer.
 *  Throws unless every entry names a feature and has a finite value: a request
 *  built from less would present attributions nobody computed. */
export function shapValuesFromExplain(answer: ExplainResponse): CopilotShapValue[] {
  const rows = answer?.explanation;
  if (!Array.isArray(rows) || rows.length === 0) {
    throw new Error('the explain answer carried no SHAP values');
  }
  return rows.map((r) => {
    if (typeof r?.feature !== 'string' || r.feature === '' || typeof r.shap_value !== 'number' || !Number.isFinite(r.shap_value)) {
      throw new Error('the explain answer carried an unusable SHAP value');
    }
    return { feature: r.feature, shap_value: r.shap_value };
  });
}

export const copilotApi = {
  /** The model's own attributions for these inputs (the endpoint the Explain page uses). */
  shapFor: (b: ShapInputs) => api<ExplainResponse>('/predict/explain', { method: 'POST', body: JSON.stringify(b) }),
  explain: (b: CopilotRequest) => api<CopilotResponse>('/copilot/explain', { method: 'POST', body: JSON.stringify(b) }),
  health: () => api<CopilotHealth>('/copilot/health'),
  listSessions: () => api<{ sessions: CopilotSessionSummary[] }>('/copilot/sessions'),
  getSession: (id: string) => api<CopilotSessionFull>('/copilot/sessions/' + encodeURIComponent(id)),
  deleteSession: (id: string) => api<{ deleted: boolean; id: string }>('/copilot/sessions/' + encodeURIComponent(id), { method: 'DELETE' }),
  async *explainStream(b: CopilotRequest, opts?: { signal?: AbortSignal; speed?: 'fast' | 'normal' | 'slow' }): AsyncGenerator<CopilotStreamEvent, void, unknown> {
    const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api/v1';
    const qs = opts?.speed ? ('?speed=' + encodeURIComponent(opts.speed)) : '';
    const res = await fetch(base + '/copilot/explain/stream' + qs, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(b),
      signal: opts?.signal,
    });
    if (!res.ok || !res.body) { throw new Error('Stream failed: HTTP ' + res.status); }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const chunks = buf.split('\n\n');
      buf = chunks.pop() ?? '';
      for (const chunk of chunks) {
        const line = chunk.trim();
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try { yield JSON.parse(payload) as CopilotStreamEvent; } catch (err) { console.warn('SSE parse error:', err, payload); }
      }
    }
  },
};
