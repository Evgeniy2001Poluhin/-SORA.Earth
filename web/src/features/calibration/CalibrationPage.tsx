import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from "recharts";
import { calibrationApi } from "@/api/endpoints/calibration";
import type { DiscrepancyResponse, ExplainLocalRequest } from "@/api/types";
import "./calibration.css";
import { BrierReliabilityPanel } from "./BrierReliabilityPanel";

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

const REC_STYLE: Record<string, { label: string; color: string; bg: string }> = {
  consensus:             { label: "CONSENSUS",             color: "#2FE0A6", bg: "rgba(47,224,166,0.12)" },
  moderate_disagreement: { label: "MODERATE DISAGREEMENT", color: "#F5C84B", bg: "rgba(245,200,75,0.12)" },
  high_disagreement:     { label: "HIGH DISAGREEMENT",     color: "#EF4444", bg: "rgba(239,68,68,0.12)" },
};

const MODEL_COLOR: Record<string, string> = {
  rf_v1:         "#7AA2F7",
  stacking_v2:   "#BB9AF7",
  calibrated_v2: "#2FE0A6",
};

export function CalibrationPage() {
  const { register, handleSubmit, reset } = useForm<FormValues>({
    defaultValues: PRESETS.MED,
  });
  const [data, setData] = useState<DiscrepancyResponse | null>(null);

  const mut = useMutation({
    mutationFn: (b: ExplainLocalRequest) => calibrationApi.discrepancy(b),
    onSuccess: (r) => { setData(r); toast.success("Discrepancy computed"); },
    onError: (e: any) => toast.error("Failed: " + (e?.message ?? "unknown")),
  });

  const submit = (v: FormValues) => mut.mutate(v);
  const usePreset = (k: PresetKey) => { reset(PRESETS[k]); mut.mutate(PRESETS[k]); };

  const chartData = data ? [
    { model: "rf_v1",         proba: data.models.rf_v1.proba,         weight: data.models.rf_v1.weight },
    { model: "stacking_v2",   proba: data.models.stacking_v2.proba,   weight: data.models.stacking_v2.weight },
    { model: "calibrated_v2", proba: data.models.calibrated_v2.proba, weight: data.models.calibrated_v2.weight },
  ] : [];

  const rec = data ? REC_STYLE[data.recommendation] ?? REC_STYLE.consensus : null;

  return (
    <div className="card-body" style={{ padding: 32 }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>Calibration / Cross-Model Trust</div>
      <h1 className="display" style={{ fontSize: 36, margin: "0 0 8px" }}>Do our models agree?</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, marginBottom: 28 }}>
        Three independent models vote on the same project. Consensus = trust. Disagreement = flag.
      </p>

      <div className="calib-presets">
        {PRESET_KEYS.map((k) => (
          <button key={k} type="button" className="preset-btn" onClick={() => usePreset(k)}>{k}</button>
        ))}
      </div>

      <form onSubmit={handleSubmit(submit)} className="calib-form">
        <label><span>Budget USD</span><input type="number" step="any" {...register("budget", { valueAsNumber: true })} /></label>
        <label><span>CO2 t/y</span><input type="number" step="any" {...register("co2_reduction", { valueAsNumber: true })} /></label>
        <label><span>Social (0-10)</span><input type="number" step="any" {...register("social_impact", { valueAsNumber: true })} /></label>
        <label><span>Duration mo.</span><input type="number" step="1" {...register("duration_months", { valueAsNumber: true })} /></label>
        <button type="submit" className="primary-btn" disabled={mut.isPending}>
          {mut.isPending ? "Computing..." : "Compare models"}
        </button>
      </form>

      {data && rec && (
        <>
          <div className="rec-banner" style={{ background: rec.bg, color: rec.color, borderColor: rec.color }}>
            <span className="rec-dot" style={{ background: rec.color }} />
            <span className="rec-label">{rec.label}</span>
            <span className="rec-spread">max spread: {(data.divergence.max_spread * 100).toFixed(1)}% / std: {(data.divergence.std * 100).toFixed(2)}%</span>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginTop: 20, marginBottom: 24 }}>
            <div className="kpi"><div className="kpi-lbl">Weighted proba</div><div className="kpi-val tabular">{(data.consensus.weighted_proba * 100).toFixed(1)}%</div></div>
            <div className="kpi"><div className="kpi-lbl">Tree CI 90%</div><div className="kpi-val tabular" style={{ fontSize: 16 }}>{(data.tree_uncertainty.ci_90[0] * 100).toFixed(1)}-{(data.tree_uncertainty.ci_90[1] * 100).toFixed(1)}%</div></div>
            <div className="kpi"><div className="kpi-lbl">Tree std</div><div className="kpi-val tabular">{(data.tree_uncertainty.std * 100).toFixed(2)}%</div></div>
            <div className="kpi"><div className="kpi-lbl">N trees</div><div className="kpi-val tabular">{data.tree_uncertainty.n_trees}</div></div>
          </div>

          <div className="eyebrow" style={{ marginBottom: 12 }}>Per-model probability</div>
          <div className="chart-wrap">
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={chartData} margin={{ top: 16, right: 24, left: 0, bottom: 8 }}>
                <XAxis dataKey="model" stroke="#8aa" tick={{ fontSize: 12 }} />
                <YAxis stroke="#8aa" domain={[0, 1]} tick={{ fontSize: 12 }} tickFormatter={(v) => (v * 100).toFixed(0) + "%"} />
                <Tooltip contentStyle={{ background: "#0d1814", border: "1px solid #234", borderRadius: 8, fontSize: 12 }} formatter={(v: any) => (Number(v) * 100).toFixed(2) + "%"} />
                <ReferenceLine y={data.consensus.weighted_proba} stroke="#2FE0A6" strokeDasharray="4 4" />
                <Bar dataKey="proba" radius={[6, 6, 0, 0]}>
                  {chartData.map((d) => (<Cell key={d.model} fill={MODEL_COLOR[d.model] ?? "#7AA2F7"} />))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="model-meta">
            {chartData.map((d) => (
              <div key={d.model} className="model-meta-row">
                <span className="model-dot" style={{ background: MODEL_COLOR[d.model] }} />
                <span className="mono">{d.model}</span>
                <span className="tabular muted">proba {(d.proba * 100).toFixed(2)}%</span>
                <span className="tabular muted">weight {(d.weight * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </>
      )}

      {!data && (
        <div className="placeholder">Click a preset or fill the form to compare models</div>
      )}
      <BrierReliabilityPanel />

    </div>
  );
}
