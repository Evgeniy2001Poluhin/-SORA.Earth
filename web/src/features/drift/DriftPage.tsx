import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

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
  status: "stable" | "drift_detected" | "insufficient_data";
  timestamp: string;
  observations: number;
  drift_detected: boolean;
  drift_score: number;
  drifted_features: string[];
  features: Record<string, FeatureStat>;
  recent_alerts: Array<{ feature: string; drift_level: string; message: string }>;
};

const LVL_COLOR: Record<string, string> = {
  LOW: "#2FE0A6",
  MEDIUM: "#F5C84B",
  HIGH: "#EF4444",
};

export function DriftPage() {
  const q = useQuery({
    queryKey: ["drift"],
    queryFn: () => api<DriftResponse>("/mlops/drift"),
    refetchInterval: 5000,
  });

  if (q.isLoading) return <div className="card-body"><p style={{ color: "var(--muted)" }}>Loading drift status...</p></div>;
  if (q.isError) return <div className="card-body"><p style={{ color: "#EF4444" }}>Failed to load drift status</p></div>;

  const d = q.data!;
  const isStable = d.status === "stable";
  const features = Object.entries(d.features || {}).sort(
    (a, b) => Math.abs(b[1].z_score) - Math.abs(a[1].z_score)
  );

  return (
    <div className="card-body" style={{ padding: 32 }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>MLOps · Model Monitoring</div>
      <h1 className="display" style={{ fontSize: 36, margin: "0 0 8px" }}>Feature Drift</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, marginBottom: 28 }}>
        Real-time KS-style drift detection across {Object.keys(d.features || {}).length} model features.
        Auto-refresh every 5s.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 32 }}>
        <div className="kpi">
          <div className="kpi-lbl">Status</div>
          <div className="kpi-val" style={{ color: isStable ? "#2FE0A6" : "#EF4444", fontSize: 18 }}>
            {isStable ? "STABLE" : "DRIFT DETECTED"}
          </div>
        </div>
        <div className="kpi">
          <div className="kpi-lbl">Drift score</div>
          <div className="kpi-val tabular">{(d.drift_score * 100).toFixed(0)}%</div>
        </div>
        <div className="kpi">
          <div className="kpi-lbl">Observations</div>
          <div className="kpi-val tabular">{d.observations}</div>
        </div>
        <div className="kpi">
          <div className="kpi-lbl">Drifted features</div>
          <div className="kpi-val tabular" style={{ color: d.drifted_features.length ? "#EF4444" : "var(--text)" }}>
            {d.drifted_features.length}
          </div>
        </div>
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
            <div className="tabular" style={{ color: "var(--muted)" }}>{f.baseline_mean.toFixed(3)}</div>
            <div className="tabular">{f.current_mean.toFixed(3)}</div>
            <div className="tabular" style={{ color: f.z_score >= 2 ? "#EF4444" : "var(--text)" }}>
              {f.z_score.toFixed(2)}
            </div>
            <div>
              <span className="drift-pill" style={{
                background: `${LVL_COLOR[f.drift_level]}22`,
                color: LVL_COLOR[f.drift_level],
                border: `1px solid ${LVL_COLOR[f.drift_level]}55`
              }}>
                {f.drift_level}
              </span>
            </div>
          </div>
        ))}
      </div>

      {d.recent_alerts && d.recent_alerts.length > 0 && (
        <>
          <div className="eyebrow" style={{ marginTop: 32, marginBottom: 12 }}>Active alerts</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {d.recent_alerts.map((a, i) => (
              <div key={i} style={{
                background: "rgba(239,68,68,0.08)",
                border: "1px solid rgba(239,68,68,0.3)",
                borderRadius: 8, padding: "12px 16px",
                display: "flex", alignItems: "center", gap: 12
              }}>
                <span style={{ color: "#EF4444", fontSize: 18 }}>⚠</span>
                <span className="mono" style={{ fontSize: 12 }}>{a.feature}</span>
                <span style={{ color: "var(--muted)", fontSize: 13 }}>{a.message}</span>
              </div>
            ))}
          </div>
        </>
      )}

      <p style={{ color: "var(--faint)", fontSize: 11, marginTop: 32, fontFamily: "var(--f-mono)" }}>
        last update: {new Date(d.timestamp).toLocaleTimeString()}
      </p>
    </div>
  );
}
