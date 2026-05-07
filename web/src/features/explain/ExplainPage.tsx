import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { explainApi } from "@/api/endpoints/explain";
import type { ExplainLocalRequest, ExplainLocalResponse } from "@/api/types";
import "./explain.css";

interface FormValues {
  budget: number;
  co2_reduction: number;
  social_impact: number;
  duration_months: number;
}
type PresetKey = "WEAK" | "MED" | "BIG";

const PRESETS: Record<PresetKey, FormValues> = {
  WEAK: { budget: 5000,   co2_reduction: 10,   social_impact: 2, duration_months: 3  },
  MED:  { budget: 100000, co2_reduction: 250,  social_impact: 7, duration_months: 24 },
  BIG:  { budget: 500000, co2_reduction: 1000, social_impact: 9, duration_months: 36 },
};
const PRESET_KEYS: PresetKey[] = ["WEAK", "MED", "BIG"];

export function ExplainPage() {
  const { register, handleSubmit, reset } = useForm<FormValues>({
    defaultValues: PRESETS.MED,
  });
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [json, setJson] = useState<ExplainLocalResponse | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);

  const mut = useMutation({
    mutationFn: async (b: ExplainLocalRequest) => {
      const t0 = performance.now();
      const [j, blob] = await Promise.all([
        explainApi.local(b),
        explainApi.waterfallBlob(b),
      ]);
      return { j, blob, ms: performance.now() - t0 };
    },
    onSuccess: ({ j, blob, ms }) => {
      setJson(j);
      if (imgUrl) URL.revokeObjectURL(imgUrl);
      setImgUrl(URL.createObjectURL(blob));
      setElapsed(ms);
      toast.success("Rendered in " + ms.toFixed(0) + "ms");
    },
    onError: (e: any) => toast.error("Explain failed: " + (e?.message ?? "unknown")),
  });

  const submit = (v: FormValues) => mut.mutate(v);
  const usePreset = (k: PresetKey) => { reset(PRESETS[k]); mut.mutate(PRESETS[k]); };

  const contribs = json?.top_contributions ?? [];
  const maxAbs = Math.max(1e-9, ...contribs.map((c) => Math.abs(c.shap_value)));

  return (
    <div className="card-body" style={{ padding: 32 }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>Explainability / SHAP Lab</div>
      <h1 className="display" style={{ fontSize: 36, margin: "0 0 8px" }}>Why this prediction?</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, marginBottom: 28 }}>
        Server-rendered SHAP waterfall, disk-cached by sha1(payload). First call ~500ms, repeats ~14ms.
      </p>

      <div className="explain-presets">
        {PRESET_KEYS.map((k) => (
          <button key={k} type="button" className="preset-btn" onClick={() => usePreset(k)}>{k}</button>
        ))}
        {elapsed !== null && (
          <span className="elapsed-pill">
            {elapsed.toFixed(0)}ms {elapsed < 100 ? "cached" : "cold"}
          </span>
        )}
      </div>

      <form onSubmit={handleSubmit(submit)} className="explain-form">
        <label><span>Budget USD</span><input type="number" step="any" {...register("budget", { valueAsNumber: true })} /></label>
        <label><span>CO2 t/y</span><input type="number" step="any" {...register("co2_reduction", { valueAsNumber: true })} /></label>
        <label><span>Social (0-10)</span><input type="number" step="any" {...register("social_impact", { valueAsNumber: true })} /></label>
        <label><span>Duration mo.</span><input type="number" step="1" {...register("duration_months", { valueAsNumber: true })} /></label>
        <button type="submit" className="primary-btn" disabled={mut.isPending}>
          {mut.isPending ? "Computing..." : "Explain"}
        </button>
      </form>

      {json && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginTop: 28, marginBottom: 24 }}>
          <div className="kpi"><div className="kpi-lbl">Prediction</div><div className="kpi-val tabular">{json.prediction != null ? json.prediction.toFixed(3) : "—"}</div></div>
          <div className="kpi"><div className="kpi-lbl">Base E[f(x)]</div><div className="kpi-val tabular">{json.base_value != null ? json.base_value.toFixed(3) : "—"}</div></div>
          <div className="kpi"><div className="kpi-lbl">Delta from base</div><div className="kpi-val tabular" style={{ color: (json.prediction ?? 0) >= (json.base_value ?? 0) ? "#2FE0A6" : "#EF4444" }}>{json.prediction != null && json.base_value != null ? ((json.prediction - json.base_value >= 0 ? "+" : "") + (json.prediction - json.base_value).toFixed(3)) : "—"}</div></div>
        </div>
      )}

      <div className="explain-grid">
        <div className="explain-img-wrap">
          <div className="eyebrow" style={{ marginBottom: 8 }}>SHAP waterfall</div>
          {imgUrl ? <img src={imgUrl} alt="SHAP waterfall" className="explain-img" /> : <div className="placeholder">Click a preset or fill the form</div>}
        </div>
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>Top contributions</div>
          {contribs.length === 0 ? (
            <div className="placeholder small">No data yet</div>
          ) : (
            <div className="contrib-list">
              {contribs.slice(0, 10).map((c) => {
                const pct = (Math.abs(c.shap_value) / maxAbs) * 100;
                const positive = c.shap_value >= 0;
                return (
                  <div key={c.feature} className="contrib-row">
                    <div className="mono contrib-name">{c.feature}</div>
                    <div className="contrib-bar-wrap">
                      <div className="contrib-bar" style={{ width: pct + "%", background: positive ? "#2FE0A6" : "#EF4444" }} />
                    </div>
                    <div className="tabular contrib-val" style={{ color: positive ? "#2FE0A6" : "#EF4444" }}>
                      {(positive ? "+" : "") + (c.shap_value ?? 0).toFixed(3)}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
