import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { copilotApi } from "@/api/endpoints/copilot";
import type { CopilotRequest, CopilotResponse } from "@/api/endpoints/copilot";
import "./copilot.css";

interface FormValues {
  probability: number;
  budget: number;
  co2_reduction: number;
  social_impact: number;
  duration_months: number;
  co2_per_dollar: number;
}

type PresetKey = "HIGH" | "MODERATE" | "LOW";

const PRESETS: Record<PresetKey, FormValues> = {
  HIGH:     { probability: 0.87, budget: 2000000, co2_reduction: 8000, social_impact: 85, duration_months: 18, co2_per_dollar: 4.0 },
  MODERATE: { probability: 0.55, budget: 1500000, co2_reduction: 3000, social_impact: 60, duration_months: 24, co2_per_dollar: 2.0 },
  LOW:      { probability: 0.22, budget: 5000000, co2_reduction: 800,  social_impact: 30, duration_months: 36, co2_per_dollar: 0.4 },
};

const PRESET_KEYS: PresetKey[] = ["HIGH", "MODERATE", "LOW"];

export function CopilotPage() {
  const { register, handleSubmit, reset, watch } = useForm<FormValues>({ defaultValues: PRESETS.HIGH });
  const [result, setResult] = useState<CopilotResponse | null>(null);
  const probability = watch("probability");

  const mut = useMutation({
    mutationFn: async (v: FormValues) => {
      const body: CopilotRequest = {
        probability: Number(v.probability),
        features: {
          budget: Number(v.budget),
          co2_reduction: Number(v.co2_reduction),
          social_impact: Number(v.social_impact),
          duration_months: Number(v.duration_months),
          co2_per_dollar: Number(v.co2_per_dollar),
        },
        shap_values: [
          { feature: "co2_reduction", shap_value: 0.12 },
          { feature: "social_impact", shap_value: 0.08 },
          { feature: "budget", shap_value: -0.04 },
        ],
      };
      return copilotApi.explain(body);
    },
    onSuccess: (r) => { setResult(r); toast.success("Explanation generated"); },
    onError: (e: any) => toast.error("Failed: " + (e?.message ?? "unknown")),
  });

  const submit = (v: FormValues) => mut.mutate(v);
  const usePreset = (k: PresetKey) => { reset(PRESETS[k]); mut.mutate(PRESETS[k]); };

  return (
    <div className="copilot-page">
      <div className="copilot-header">
        <div className="eyebrow">AI Co-Pilot · Explainability Layer</div>
        <h1>Why this <em style={{ color: "#5DDA92", fontStyle: "italic" }}>prediction</em>?</h1>
        <p>Translate raw SHAP attributions and probability scores into human-readable executive summaries with drivers, risks, and recommendations.</p>
      </div>

      <div className="copilot-grid">
        <form className="copilot-form" onSubmit={handleSubmit(submit)}>
          <h3>Project Inputs</h3>

          <div className="preset-row">
            {PRESET_KEYS.map((k) => (
              <button key={k} type="button" className="preset-btn" onClick={() => usePreset(k)}>
                {k}
              </button>
            ))}          </div>

          <div className="row">
            <label>Probability ({probability ? Number(probability).toFixed(2) : "0.50"})</label>
            <input type="range" min={0} max={1} step={0.01} {...register("probability", { valueAsNumber: true })} />
          </div>
          <div className="row"><label>Budget (USD)</label><input type="number" {...register("budget", { valueAsNumber: true })} /></div>
          <div className="row"><label>CO2 reduction (t/yr)</label><input type="number" {...register("co2_reduction", { valueAsNumber: true })} /></div>
          <div className="row"><label>Social impact (0-100)</label><input type="number" {...register("social_impact", { valueAsNumber: true })} /></div>
          <div className="row"><label>Duration (months)</label><input type="number" {...register("duration_months", { valueAsNumber: true })} /></div>
          <div className="row"><label>CO2 per dollar</label><input type="number" step="0.1" {...register("co2_per_dollar", { valueAsNumber: true })} /></div>

          <button className="btn-primary" type="submit" disabled={mut.isPending}>
            {mut.isPending ? "Generating..." : "Generate AI Explanation"}
          </button>
        </form>

        <div className="copilot-result">
          {!result && (
            <div className="copilot-empty">
              <div className="copilot-empty-orb" />
              <p>No explanation yet</p>
              <p className="hint">Pick a preset or fill the form to get started</p>
            </div>
          )}
          {result && (
            <>
              <span className={"verdict-badge verdict-" + result.verdict.level}>
                {result.verdict.label}
              </span>
              <span className="confidence-chip">Confidence: {result.confidence}</span>

              <div className="prob-display">
                <div>
                  <span className="prob-value">{(result.probability * 100).toFixed(0)}</span>
                  <span className="prob-value-suffix">/100</span>
                </div>
                <div className="prob-value-label">ML Success Probability</div>
                <div className="prob-bar">
                  <div className="prob-bar-fill" style={{ width: (result.probability * 100) + "%" }} />
                </div>
              </div>

              {result.key_drivers.positive.length > 0 && (
                <>
                  <div className="section-title">Positive Drivers</div>
                  {result.key_drivers.positive.map((d, i) => (
                    <div key={i} className="driver-card driver-positive">
                      <span>{d.feature_label}</span>
                      {d.shap_value !== undefined && <span className="shap-value">+{d.shap_value.toFixed(3)}</span>}
                      {d.note && <span className="shap-value">{d.note}</span>}
                    </div>
                  ))}
                </>
              )}

              {result.key_drivers.negative.length > 0 && (
                <>
                  <div className="section-title neg">Negative Drivers</div>
                  {result.key_drivers.negative.map((d, i) => (
                    <div key={i} className="driver-card driver-negative">
                      <span>{d.feature_label}</span>
                      {d.shap_value !== undefined && <span className="shap-value">{d.shap_value.toFixed(3)}</span>}
                      {d.note && <span className="shap-value">{d.note}</span>}
                    </div>
                  ))}
                </>
              )}

              {result.risks.length > 0 && (
                <>
                  <div className="section-title risk">Risks</div>
                  {result.risks.map((r, i) => (
                    <div key={i} className="risk-item">{r}</div>
                  ))}
                </>
              )}

              <div className="recommendation-block">
                {result.recommendation}
              </div>

              {result.executive_summary && (
                <>
                  <div className="section-title">Executive Summary</div>
                  <p style={{ fontSize: 14, lineHeight: 1.6, color: "rgba(232,236,239,0.8)" }}>{result.executive_summary}</p>
                </>
              )}

              <div className="chips-row">
                <span className="mode-chip">Mode · {result.explanation_mode}</span>
                <span className="mode-chip">Model · {result.model_version}</span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
