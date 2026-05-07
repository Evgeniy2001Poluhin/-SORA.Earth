import { useQuery } from "@tanstack/react-query";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, CartesianGrid } from "recharts";
import { driftBaselineApi } from "@/api/endpoints/driftBaseline";
import type { MlflowDriftEvent } from "@/api/endpoints/driftBaseline";

export function DriftTimeline() {
  const tl = useQuery({ queryKey: ["drift-mlflow-history"], queryFn: driftBaselineApi.mlflowHistory, refetchInterval: 30000 });
  const ks = useQuery({ queryKey: ["drift-ks-report"], queryFn: driftBaselineApi.ksReport, refetchInterval: 30000 });

  const events: MlflowDriftEvent[] = tl.data && tl.data.events ? tl.data.events : [];
  const data = events
    .slice()
    .sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime())
    .map((e) => ({
      timeShort: new Date(e.start_time).toLocaleTimeString(),
      drift_score: Number(e["metrics.drift_score"]) || 0,
      drifted_count: Number(e["metrics.drifted_features_count"]) || 0,
      run: e.run_id.slice(0, 8),
      baseline: e["tags.baseline_id"] || "-",
      features: e["params.drifted_features"] || "-",
    }));

  const ksFeatures = ks.data && ks.data.features ? Object.entries(ks.data.features) : [];

  return (
    <div>
      <div className="eyebrow" style={{ marginBottom: 12 }}>Drift timeline (MLflow): {data.length} events</div>
      <div style={{ height: 240, background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 10, padding: 16, marginBottom: 24 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            <CartesianGrid stroke="rgba(255,255,255,0.05)" strokeDasharray="3 3" />
            <XAxis dataKey="timeShort" stroke="var(--muted)" fontSize={11} />
            <YAxis domain={[0, 1]} stroke="var(--muted)" fontSize={11} />
            <Tooltip contentStyle={{ background: "var(--bg)", border: "1px solid rgba(255,255,255,0.1)", fontSize: 12 }} />
            <ReferenceLine y={0.31} stroke="#F5C84B" strokeDasharray="4 4" label={{ value: "threshold 0.31", fill: "#F5C84B", fontSize: 10, position: "right" }} />
            <Line type="monotone" dataKey="drift_score" stroke="#2FE0A6" strokeWidth={2} dot={{ r: 4, fill: "#2FE0A6" }} activeDot={{ r: 6 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="eyebrow" style={{ marginBottom: 12 }}>Kolmogorov-Smirnov per-feature: {ksFeatures.length} features</div>
      <div className="drift-table" style={{ marginBottom: 24 }}>
        <div className="drift-row drift-head" style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1fr" }}>
          <div>Feature</div>
          <div className="tabular">KS statistic</div>
          <div className="tabular">p-value</div>
          <div>Decision</div>
        </div>
        {ksFeatures.map(([name, f]) => {
          const drift = f.drift;
          const sig = f.p_value < 0.01;
          return (
            <div key={name} className="drift-row" style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1fr" }}>
              <div className="mono" style={{ fontSize: 12 }}>{name}</div>
              <div className="tabular">{f.ks_stat.toFixed(4)}</div>
              <div className="tabular" style={{ color: sig ? "#EF4444" : "var(--muted)" }}>{f.p_value.toFixed(4)}</div>
              <div>
                <span className="drift-pill" style={{
                  background: drift ? "rgba(239,68,68,0.12)" : "rgba(47,224,166,0.12)",
                  color: drift ? "#EF4444" : "#2FE0A6",
                  border: "1px solid " + (drift ? "rgba(239,68,68,0.4)" : "rgba(47,224,166,0.4)")
                }}>{drift ? "DRIFT" : "stable"}</span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="eyebrow" style={{ marginBottom: 12 }}>MLflow drift runs</div>
      <div className="drift-table">
        <div className="drift-row drift-head" style={{ gridTemplateColumns: "80px 1fr 80px 80px 1fr 1.2fr" }}>
          <div>Run</div>
          <div>Time</div>
          <div className="tabular">Score</div>
          <div className="tabular">Drifted</div>
          <div>Baseline</div>
          <div>Features</div>
        </div>
        {data.slice().reverse().map((e) => {
          const high = e.drift_score >= 0.31;
          return (
            <div key={e.run} className="drift-row" style={{ gridTemplateColumns: "80px 1fr 80px 80px 1fr 1.2fr" }}>
              <div className="mono" style={{ fontSize: 11 }}>{e.run}</div>
              <div style={{ color: "var(--muted)", fontSize: 12 }}>{e.timeShort}</div>
              <div className="tabular" style={{ color: high ? "#EF4444" : "#2FE0A6" }}>{(e.drift_score * 100).toFixed(0)}%</div>
              <div className="tabular">{e.drifted_count}</div>
              <div className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>{e.baseline}</div>
              <div style={{ fontSize: 11, color: "var(--muted)" }}>{e.features}</div>
            </div>
          );
        })}
      </div>

      <p style={{ color: "var(--faint)", fontSize: 11, marginTop: 16, fontFamily: "var(--f-mono)" }}>
        KS-test: H0 = same distribution; reject when p &lt; 0.01. Drift score = fraction of features with rejected H0.
      </p>
    </div>
  );
}