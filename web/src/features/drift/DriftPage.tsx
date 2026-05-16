import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { driftBaselineApi } from "@/api/endpoints/driftBaseline";
import { DriftTimeline } from "./DriftTimeline";
import { api } from "@/api/client";
import "./drift.css";

type FeatureStat = {
  baseline_mean: number;
  baseline_std: number;
  current_mean: number;
  z_score: number;
  drift: boolean;
  drift_level: "LOW" | "MEDIUM" | "HIGH";
  severity: string;
};

type DriftResponse = {
  status: "stable" | "drift_detected" | "insufficient_data" | "no_baseline";
  timestamp: string;
  observations: number;
  drift_detected: boolean;
  drift_score: number;
  drifted_features: string[];
  features: Record<string, FeatureStat>;
  recent_alerts: Array<{ feature: string; drift_level: string; message: string }>;
};

export function DriftPage() {
  const q = useQuery({
    queryKey: ["drift"],
    queryFn: () => api<DriftResponse>("/mlops/drift"),
    refetchInterval: 5000,
  });
  const qc = useQueryClient();
  const baseline = useQuery({ queryKey: ["drift-baseline"], queryFn: driftBaselineApi.status });
  const fitted = baseline.data?.exists ?? false;

  const fitMut = useMutation({
    mutationFn: () => driftBaselineApi.fit(),
    onSuccess: (r) => { toast.success("Baseline fitted: " + r.n_samples + " samples"); qc.invalidateQueries({ queryKey: ["drift"] }); qc.invalidateQueries({ queryKey: ["drift-baseline"] }); },
    onError: (e: any) => toast.error("Fit failed: " + (e?.message ?? "unknown")),
  });
  const delMut = useMutation({
    mutationFn: () => driftBaselineApi.remove(),
    onSuccess: () => { toast.success("Baseline deleted"); qc.invalidateQueries({ queryKey: ["drift"] }); qc.invalidateQueries({ queryKey: ["drift-baseline"] }); },
    onError: (e: any) => toast.error("Delete failed: " + (e?.message ?? "unknown")),
  });
  const simMut = useMutation({
    mutationFn: (mode: "stable" | "drift") => driftBaselineApi.simulate(mode, 50),
    onSuccess: (_r, mode) => { toast.success("Simulated " + mode); qc.invalidateQueries({ queryKey: ["drift"] }); },
    onError: (e: any) => toast.error("Simulate failed: " + (e?.message ?? "unknown")),
  });

  if (q.isLoading) return <div className="card-body"><p className="muted">Loading drift status...</p></div>;
  if (q.isError) return <div className="card-body"><p className="danger">Failed to load drift status</p></div>;

  const d = q.data;
  if (!d) {
    return <div className="card-body"><p className="muted">No drift data available yet. Waiting for first observation...</p></div>;
  }

  const isStable = d.status === "stable";
  const noBaseline = d.status === "no_baseline";
  const bannerStatus = noBaseline ? "no_baseline" : (isStable ? "stable" : "drift");
  const statusLabel = noBaseline ? "NO BASELINE" : (isStable ? "STABLE" : "DRIFT DETECTED");
  const statusIcon = noBaseline ? "○" : (isStable ? "✓" : "⚠");

  const features = Object.entries(d.features || {}).sort(
    (a, b) => Math.abs(b[1].z_score) - Math.abs(a[1].z_score)
  );

  return (
    <div className="card-body" style={{ padding: 32 }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>MLOps · Model Monitoring</div>
      <h1 className="display" style={{ fontSize: 36, margin: "0 0 8px" }}>Feature Drift</h1>
      <p className="muted" style={{ fontSize: 14, marginBottom: 28 }}>
        Real-time KS-style drift detection across {Object.keys(d.features || {}).length} model features. Auto-refresh every 5s.
      </p>

      <div className="drift-banner" data-status={bannerStatus}>
        <span className="icon">{statusIcon}</span>
        <div className="body">
          <div className="label">Pipeline status</div>
          <div className="title">{statusLabel}</div>
        </div>
        <div className="meta">
          drift score · {(d.drift_score * 100).toFixed(0)}%<br/>
          {d.drifted_features?.length ?? 0} drifted / {Object.keys(d.features || {}).length} features
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 32 }}>
        <div className="kpi"><div className="kpi-lbl">Status</div><div className="kpi-val" style={{ fontSize: 18 }}>{statusLabel}</div></div>
        <div className="kpi"><div className="kpi-lbl">Drift score</div><div className="kpi-val tabular">{(d.drift_score * 100).toFixed(0)}%</div></div>
        <div className="kpi"><div className="kpi-lbl">Observations</div><div className="kpi-val tabular">{d.observations}</div></div>
        <div className="kpi"><div className="kpi-lbl">Drifted features</div><div className="kpi-val tabular">{d.drifted_features?.length ?? 0}</div></div>
      </div>

      <div className="eyebrow" style={{ marginBottom: 12 }}>Baseline controls</div>
      <div style={{ display: "flex", gap: 12, marginBottom: 24, alignItems: "center", flexWrap: "wrap" }}>
        <div className="kpi" style={{ minWidth: 220 }}>
          <div className="kpi-lbl">Baseline</div>
          <div className="kpi-val tabular" style={{ fontSize: 14 }}>
            {baseline.data?.exists
              ? (baseline.data.n_samples + " samples / " + (baseline.data.feature_count ?? "?") + " feats")
              : "not fitted"}
          </div>
        </div>
        <button className="preset-btn" disabled={fitMut.isPending} onClick={() => fitMut.mutate()}>{fitMut.isPending ? "Fitting..." : "Fit baseline"}</button>
        <button className="preset-btn" disabled={delMut.isPending || !fitted} onClick={() => delMut.mutate()}>{delMut.isPending ? "Deleting..." : "Delete baseline"}</button>
        <button className="preset-btn" disabled={simMut.isPending || !fitted} onClick={() => simMut.mutate("stable")}>Simulate stable</button>
        <button className="preset-btn" disabled={simMut.isPending || !fitted} onClick={() => simMut.mutate("drift")}>Simulate drift</button>
      </div>

      <div className="eyebrow" style={{ marginBottom: 12 }}>Feature breakdown</div>
      <div className="drift-table">
        <div className="drift-row drift-head">
          <div>Feature</div>
          <div className="tabular">Baseline μ</div>
          <div className="tabular">Current μ</div>
          <div className="tabular">|z|</div>
          <div>Severity</div>
        </div>
        {features.map(([name, f]) => (
          <div key={name} className="drift-row">
            <div className="mono" style={{ fontSize: 12 }}>{name}</div>
            <div className="tabular muted">{f.baseline_mean.toFixed(3)}</div>
            <div className="tabular">{f.current_mean.toFixed(3)}</div>
            <div className="tabular">{f.z_score.toFixed(2)}</div>
            <div><span className="drift-pill" data-level={f.drift_level}>{f.drift_level}</span></div>
          </div>
        ))}
      </div>

      {d.recent_alerts && d.recent_alerts.length > 0 && (
        <>
          <div className="eyebrow" style={{ marginTop: 32, marginBottom: 12 }}>Active alerts</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {d.recent_alerts.map((a, i) => (
              <div key={i} className="drift-alert">
                <span className="icon">⚠</span>
                <span className="feature">{a.feature}</span>
                <span className="msg">{a.message}</span>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="eyebrow" style={{ marginTop: 32, marginBottom: 12 }}>Temporal trend</div>
      <DriftTimeline/>

      <p className="faint mono" style={{ fontSize: 11, marginTop: 32 }}>
        last update: {d.timestamp ? new Date(d.timestamp).toLocaleTimeString() : "—"}
      </p>
    </div>
  );
}
