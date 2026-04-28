import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { mlopsApi } from "@/api/endpoints/mlops";
import "./mlops.css";

export function MlopsHealthPage() {
  const h  = useQuery({ queryKey: ["mlops-health"], queryFn: mlopsApi.health, refetchInterval: 10000 });
  const st = useQuery({ queryKey: ["model-status"], queryFn: mlopsApi.modelStatus });
  const m  = useQuery({ queryKey: ["model-metrics"], queryFn: mlopsApi.modelMetrics });

  if (h.isLoading || st.isLoading || m.isLoading)
    return <div className="card-body"><p style={{ color: "var(--muted)" }}>Loading MLOps status...</p></div>;
  if (h.isError || st.isError || m.isError)
    return <div className="card-body"><p style={{ color: "#EF4444" }}>Failed to load MLOps status</p></div>;

  const health  = h.data!;
  const status  = st.data!;
  const metrics = m.data!;
  const meta    = status.meta;

  const driftColor = health.drift_status === "stable" || health.drift_status === "no_baseline" ? "#2FE0A6" : "#EF4444";
  const modelColor = health.model_status === "healthy" ? "#2FE0A6" : "#EF4444";

  const metricBars = [
    { name: "accuracy", value: metrics.metrics.accuracy },
    { name: "f1",       value: metrics.metrics.f1_score },
    { name: "roc_auc",  value: metrics.metrics.roc_auc },
    { name: "best_f1",  value: metrics.metrics.best_f1 },
  ];

  const hist = (status.retrain_history || []).slice().reverse();

  return (
    <div className="card-body" style={{ padding: 32 }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>MLOps · Platform Health</div>
      <h1 className="display" style={{ fontSize: 36, margin: "0 0 8" }}>MLOps Control Room</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, marginBottom: 28 }}>
        Model serving, drift detection, training history. Auto-refresh every 10s.
      </p>

      <div className="kpi-grid">
        <div className="kpi">
          <div className="kpi-lbl">Model</div>
          <div className="kpi-val" style={{ color: modelColor, fontSize: 18 }}>{health.model_status.toUpperCase()}</div>
        </div>
        <div className="kpi">
          <div className="kpi-lbl">Drift</div>
          <div className="kpi-val" style={{ color: driftColor, fontSize: 18 }}>{health.drift_status.replace("_", " ").toUpperCase()}</div>
        </div>
        <div className="kpi">
          <div className="kpi-lbl">Observations</div>
          <div className="kpi-val tabular">{health.observations_tracked}</div>
        </div>
        <div className="kpi">
          <div className="kpi-lbl">Threshold</div>
          <div className="kpi-val tabular">{status.current_threshold.toFixed(2)}</div>
        </div>
      </div>

      <div className="mlops-section">
        <div className="eyebrow" style={{ marginBottom: 12 }}>Active model</div>
        <div className="kpi-grid">
          <div className="kpi"><div className="kpi-lbl">Algorithm</div><div className="kpi-val mono" style={{ fontSize: 13 }}>{meta.algorithm}</div></div>
          <div className="kpi"><div className="kpi-lbl">N estimators</div><div className="kpi-val tabular">{meta.n_estimators}</div></div>
          <div className="kpi"><div className="kpi-lbl">Max depth</div><div className="kpi-val tabular">{meta.max_depth}</div></div>
          <div className="kpi"><div className="kpi-lbl">Train samples</div><div className="kpi-val tabular">{meta.total_samples}</div></div>
        </div>
        <p style={{ color: "var(--faint)", fontSize: 11, marginTop: 12, fontFamily: "var(--f-mono)" }}>retrained_at: {meta.retrained_at}</p>
      </div>

      <div className="mlops-section">
        <div className="eyebrow" style={{ marginBottom: 12 }}>Performance metrics</div>
        <div style={{ height: 220, background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 10, padding: 16 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={metricBars}>
              <XAxis dataKey="name" stroke="var(--muted)" fontSize={12}/>
              <YAxis domain={[0, 1]} stroke="var(--muted)" fontSize={12}/>
              <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} contentStyle={{ background: "var(--bg)", border: "1px solid rgba(255,255,255,0.1)", fontSize: 12 }}/>
              <Bar dataKey="value" fill="#2FE0A6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="mlops-section">
        <div className="eyebrow" style={{ marginBottom: 12 }}>Retrain history · {hist.length}</div>
        <div className="mlops-table">
          <div className="mlops-row mlops-head">
            <div>Version</div><div>Trigger</div><div className="tabular">Dur</div><div className="tabular">Acc</div><div className="tabular">F1</div><div className="tabular">AUC</div><div>Status</div>
          </div>
          {hist.map((r) => (
            <div key={r.model_version || r.started_at} className="mlops-row">
              <div className="mono" style={{ fontSize: 11 }}>{r.model_version || "—"}</div>
              <div style={{ color: "var(--muted)", fontSize: 12 }}>{r.trigger_source || "—"}</div>
              <div className="tabular">{r.duration_sec ? r.duration_sec.toFixed(1) + "s" : "—"}</div>
              <div className="tabular">{r.metrics?.accuracy?.toFixed(3) ?? "—"}</div>
              <div className="tabular">{r.metrics?.f1_score?.toFixed(3) ?? "—"}</div>
              <div className="tabular">{r.metrics?.roc_auc?.toFixed(3) ?? "—"}</div>
              <div><span className="pill" style={{ color: r.status === "success" ? "#2FE0A6" : "#EF4444", background: r.status === "success" ? "rgba(47,224,166,0.12)" : "rgba(239,68,68,0.12)" }}>{r.status}</span></div>
            </div>
          ))}
        </div>
      </div>

      <div className="mlops-section">
        <div className="eyebrow" style={{ marginBottom: 12 }}>Model artifacts · {metrics.models_available.length}</div>
        <div className="mlops-chips">
          {metrics.models_available.map((f) => (
            <span key={f} className="mlops-chip">{f}</span>
          ))}
        </div>
      </div>

      <div className="mlops-section" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div className="kpi locked-tile">
          <div className="locked-badge">🔒 Sign-in required</div>
          <div className="kpi-lbl">Admin retrain log</div>
          <div className="kpi-val" style={{ fontSize: 14, color: "var(--muted)" }}>/api/v1/admin/retrain-log</div>
        </div>
        <div className="kpi locked-tile">
          <div className="locked-badge">🔒 API key required</div>
          <div className="kpi-lbl">Feature importance</div>
          <div className="kpi-val" style={{ fontSize: 14, color: "var(--muted)" }}>/api/v1/model/feature-importance</div>
        </div>
      </div>
    </div>
  );
}