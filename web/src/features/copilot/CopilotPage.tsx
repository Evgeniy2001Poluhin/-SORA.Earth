import { useState, useRef, useCallback } from "react";
import { MarkdownAnswer } from "./MarkdownAnswer";
import { SessionsSidebar } from "./SessionsSidebar";
import { useMutation } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { copilotApi } from "@/api/endpoints/copilot";
import type { CopilotRequest, CopilotResponse } from "@/api/endpoints/copilot";
import "./copilot.css";
import { errorMessage } from "@/lib/errors";

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
  const { register, handleSubmit, reset, control } = useForm<FormValues>({ defaultValues: PRESETS.HIGH });
  const [result, setResult] = useState<CopilotResponse | null>(null);
  const [activePreset, setActivePreset] = useState<PresetKey | null>("HIGH");
  // useWatch rather than watch(): the value is only read to render a label, and
  // watch() returns a new function result on every render, which the React
  // Compiler cannot memoize.
  const probability = useWatch({ control, name: "probability" });
  const [streamMode, setStreamMode] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [sidebarTick, setSidebarTick] = useState(0);

  const loadSession = useCallback(async (id: string) => {
    try {
      const s = await copilotApi.getSession(id);
      setCurrentSessionId(s.id);
      const lastAssistant = [...s.messages].reverse().find((m) => m.role === "assistant");
      if (lastAssistant) {
        setStreamingText(lastAssistant.content);
        setResult({
          verdict: { label: "Restored", level: "info" },
          probability: 0,
          confidence: "restored",
          key_drivers: { positive: [], negative: [] },
          risks: [],
          recommendation: lastAssistant.content,
          model_version: "v5",
          explanation_mode: "restored",
        });
      }
      toast.success("Loaded chat");
    } catch (e: unknown) {
      toast.error("Load failed: " + errorMessage(e));
    }
  }, []);

  const newChat = useCallback(() => {
    setCurrentSessionId(null);
    setResult(null);
    setStreamingText("");
    setActivePreset("HIGH");
    reset(PRESETS.HIGH);
  }, [reset]);

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
      if (currentSessionId) body.session_id = currentSessionId;
      return copilotApi.explain(body);
    },
    onSuccess: (r) => { setResult(r); if (r.session_id) setCurrentSessionId(r.session_id); setSidebarTick(t=>t+1); toast.success("Explanation generated"); },
    onError: (e: unknown) => toast.error("Failed: " + errorMessage(e)),
  });

  const submit = (v: FormValues) => { setActivePreset(null); mut.mutate(v); };
  const applyPreset = (k: PresetKey) => { setActivePreset(k); reset(PRESETS[k]); mut.mutate(PRESETS[k]); };

  const runStream = async (v: FormValues) => {
    setStreaming(true);
    setStreamingText("");
    setResult(null);
    setActivePreset(null);
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
    try {
      let acc = "";
      if (currentSessionId) body.session_id = currentSessionId;
      for await (const ev of copilotApi.explainStream(body)) {
        if (ev.type === "meta") {
          if (ev.session_id) { setCurrentSessionId(ev.session_id); setSidebarTick(t=>t+1); }
        } else if (ev.type === "token") {
          acc += ev.value;
          setStreamingText(acc);
        } else if (ev.type === "done") {
          setResult({
            verdict: { label: "Streamed", level: "info" },
            probability: Number(v.probability),
            confidence: "streaming",
            key_drivers: { positive: [], negative: [] },
            risks: [],
            recommendation: acc.trim(),
            model_version: "v5",
            explanation_mode: "stream",
            sources: ev.sources,
            compliance: ev.compliance,
          });
        }
      }
      toast.success("Stream complete");
    } catch (e: unknown) {
      toast.error("Stream failed: " + errorMessage(e));
    } finally {
      setStreaming(false);
    }
  };

  return (
    <div className="copilot-layout">
      <SessionsSidebar
        currentId={currentSessionId}
        onSelect={loadSession}
        onNew={newChat}
        refreshToken={sidebarTick}
      />
      <div className="copilot-main">
    <div className="copilot-page">
      <div className="copilot-hero">
        <div className="eyebrow">AI Co-Pilot · Explainability Layer</div>
        <h1>Why this <em>prediction</em>?</h1>
        <p>Translate raw SHAP attributions and probability scores into human-readable executive summaries with drivers, risks, and recommendations for funding decisions.</p>
      </div>

      <div className="copilot-grid">
        <form className="copilot-form" onSubmit={handleSubmit(submit)}>
          <h3>Project Inputs</h3>

          <div className="preset-row">
            {PRESET_KEYS.map((k) => (
              <button
                key={k}
                type="button"
                className={"preset-btn" + (activePreset === k ? " active" : "")}
                onClick={() => applyPreset(k)}
              >
                {k}
              </button>
            ))}
          </div>

          <div className="row">
            <label>Probability ({probability ? Number(probability).toFixed(2) : "0.50"})</label>
            <input type="range" min={0} max={1} step={0.01} {...register("probability", { valueAsNumber: true })} />
          </div>
          <div className="row"><label>Budget (USD)</label><input type="number" {...register("budget", { valueAsNumber: true })} /></div>
          <div className="row"><label>CO2 reduction (t/yr)</label><input type="number" {...register("co2_reduction", { valueAsNumber: true })} /></div>
          <div className="row"><label>Social impact (0-100)</label><input type="number" {...register("social_impact", { valueAsNumber: true })} /></div>
          <div className="row"><label>Duration (months)</label><input type="number" {...register("duration_months", { valueAsNumber: true })} /></div>
          <div className="row"><label>CO2 per dollar</label><input type="number" step="0.1" {...register("co2_per_dollar", { valueAsNumber: true })} /></div>

          <div className="row" style={{display:"flex",alignItems:"center",gap:"10px",margin:"8px 0"}}>
            <label style={{display:"flex",alignItems:"center",gap:"6px",cursor:"pointer",fontSize:"13px"}}>
              <input type="checkbox" checked={streamMode} onChange={(e)=>setStreamMode(e.target.checked)} />
              <span>⃡ Stream mode (token-by-token)</span>
            </label>
            
          </div>
          {!streamMode ? (
            <button className="btn-primary" type="submit" disabled={mut.isPending || streaming}>
              {mut.isPending ? "Generating..." : "Generate AI Explanation"}
            </button>
          ) : (
            <>
              <button className="btn-primary" type="button" disabled={streaming}
                onClick={handleSubmit(runStream)}>
                {streaming ? "Streaming..." : "⡡ Stream AI Explanation"}
              </button>
              {streaming && (
                <button className="copilot-stop-btn" type="button" onClick={() => { abortRef.current?.abort(); }}>
                  Stop
                </button>
              )}
            </>
          )}
        </form>

        <div className="copilot-result">
          {streaming && (
            <div className="streaming-block" style={{padding:"20px",lineHeight:"1.6",fontSize:"14px"}}>
              <div className="section-title">⡡ Streaming...</div>
              <div style={{whiteSpace:"normal"}}><MarkdownAnswer content={streamingText} /><span className="cursor-blink">▊</span></div>
            </div>
          )}
          {!result && !streaming && (
            <div className="copilot-empty">
              <div className="copilot-empty-orb" />
              <p>No explanation yet</p>
              <p className="hint">Pick a preset or fill the form to get started</p>
            </div>
          )}
          {result && (
            <>
              <div className="result-head">
                <span className={"verdict-badge verdict-" + result.verdict.level}>
                  {result.verdict.label}
                </span>
                <span className="confidence-chip">Confidence · {result.confidence}</span>
              </div>

              <div className="prob-display">
                <span className="prob-value">{(result.probability * 100).toFixed(0)}</span>
                <span className="prob-value-suffix">/ 100</span>
                <div className="prob-value-label">ML Success Probability</div>
              </div>

              {result.key_drivers.positive.length > 0 && (
                <>
                  <div className="section-title">Positive Drivers</div>
                  {result.key_drivers.positive.map((d, i) => (
                    <div key={i} className="driver-card driver-positive">
                      <span className="driver-feature">{d.feature_label}</span>
                      {d.shap_value !== undefined && <span className="shap-value">+{d.shap_value.toFixed(3)}</span>}
                      {d.shap_value === undefined && d.note && <span className="shap-value">{d.note}</span>}
                    </div>
                  ))}
                </>
              )}

              {result.key_drivers.negative.length > 0 && (
                <>
                  <div className="section-title neg">Negative Drivers</div>
                  {result.key_drivers.negative.map((d, i) => (
                    <div key={i} className="driver-card driver-negative">
                      <span className="driver-feature">{d.feature_label}</span>
                      {d.shap_value !== undefined && <span className="shap-value">{d.shap_value.toFixed(3)}</span>}
                      {d.shap_value === undefined && d.note && <span className="shap-value">{d.note}</span>}
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
                <MarkdownAnswer content={result.recommendation} />
              </div>
              {result.sources && result.sources.length > 0 && (
                <>
                  <div className="section-title" style={{marginTop: 22}}>Sources Retrieved Knowledge</div>
                  {result.sources.map((s, i) => (
                    <div key={i} className="source-card">
                      <div className="source-head">
                        <span className="source-title">{s.title}</span>
                        <span className="source-meta">
                          <span className={"cat-chip cat-" + s.category}>{s.category.replace("_"," ")}</span>
                          <span className="score-chip">{Math.round(s.score * 100)}% match</span>
                        </span>
                      </div>
                      <p className="source-excerpt">{s.excerpt}</p>
                    </div>
                  ))}
                </>
              )}

              {result.compliance && (
                <div className={"sentinel-badge sentinel-" + (result.compliance.passed ? "pass" : (result.compliance.risk_score >= 0.5 ? "fail" : "warn"))}>
                  <span className="sentinel-icon">{result.compliance.passed ? "✓" : "⚠"}</span>
                  <span className="sentinel-label">SENTINEL · {result.compliance.passed ? "PASS" : (result.compliance.risk_score >= 0.5 ? "FAIL" : "WARN")}</span>
                  <span className="sentinel-meta">risk {(result.compliance.risk_score * 100).toFixed(0)}%</span>
                  {result.compliance.pii_findings.length > 0 && (
                    <span className="sentinel-meta">PII {result.compliance.pii_findings.length}</span>
                  )}
                  {result.compliance.bias_findings.length > 0 && (
                    <span className="sentinel-meta">bias {result.compliance.bias_findings.length}</span>
                  )}
                  {result.compliance.policy_violations.length > 0 && (
                    <span className="sentinel-meta">policy {result.compliance.policy_violations.length}</span>
                  )}
                </div>
              )}



              {result.executive_summary && (
                <>
                  <div className="section-title">Executive Summary</div>
                  <p className="exec-summary">{result.executive_summary}</p>
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
      </div>
    </div>
  );
}