import { useQuery } from "@tanstack/react-query";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, CartesianGrid } from "recharts";
import { driftBaselineApi } from "@/api/endpoints/driftBaseline";
import type { MlflowDriftEvent, KsFeatureStat } from "@/api/endpoints/driftBaseline";

/** Rows the server requires before it will run a KS test (`MIN_WINDOW_ROWS`).
 *  Quoted only to make the "not enough data" line specific; the server decides. */
const MIN_KS_ROWS = 10;

export function DriftTimeline() {
  const tl = useQuery({ queryKey: ["drift-mlflow-history"], queryFn: driftBaselineApi.mlflowHistory, refetchInterval: 30000 });
  const ks = useQuery({ queryKey: ["drift-ks-report"], queryFn: driftBaselineApi.ksReport, refetchInterval: 30000 });

  // Guarded on the array as well as the status. The status is the server's
  // contract, but a client that reads `events` on the strength of a string is
  // exactly what #236 was about -- and it is not hypothetical here: the KS
  // report next door also answers `status: "ok"`, with no `events` at all.
  const events: MlflowDriftEvent[] =
    tl.data?.status === "ok" && Array.isArray(tl.data.events) ? tl.data.events : [];

  // "0 events" used to cover two different worlds: MLflow answered and holds
  // nothing, or MLflow could not be reached at all. The second is now a 503 and
  // arrives as a rejection, so the two are told apart here rather than counted
  // together.
  const timelineLabel = (): string => {
    if (tl.isLoading) return "loading…";
    if (tl.isError) return "unavailable — MLflow could not be queried";
    return `${data.length} events`;
  };
  // The payload-level guard above is the correct shape already. What was
  // missing is the same question one level down: every field here has a
  // fallback except `run_id`, so a single event without one threw on
  // `.slice` and took the whole drift page with it -- the timeline renders
  // inside DriftPage. An absent `start_time` was quieter and worse: it sorts
  // as NaN, which reorders the timeline rather than failing (#236).
  const at = (e: MlflowDriftEvent) => new Date(e.start_time).getTime();
  const data = events
    .slice()
    .sort((a, b) => (Number.isFinite(at(a)) ? at(a) : 0) - (Number.isFinite(at(b)) ? at(b) : 0))
    .map((e) => ({
      timeShort: Number.isFinite(at(e)) ? new Date(at(e)).toLocaleTimeString() : "-",
      drift_score: Number(e["metrics.drift_score"]) || 0,
      drifted_count: Number(e["metrics.drifted_features_count"]) || 0,
      run: typeof e.run_id === "string" ? e.run_id.slice(0, 8) : "-",
      baseline: e["tags.baseline_id"] || "-",
      features: e["params.drifted_features"] || "-",
    }));

  // #239. The KS table used to be labelled "N features" with N counted from
  // whatever `features` happened to hold, so "there is no prediction log yet"
  // reached the screen as "0 features" -- a measurement nobody took. The server
  // now says which of those it is, and every case is answered here by name
  // rather than by counting.
  const ksFeatures: Array<[string, KsFeatureStat]> =
    ks.data?.status === "ok" && ks.data.features ? Object.entries(ks.data.features) : [];

  const ksLabel = (): string => {
    if (ks.isLoading) return "loading…";
    // A 503 rejects in the client, so an unavailable service arrives here as an
    // error, not as an answer. It must not read as "no drift".
    if (ks.isError) return "unavailable — the test could not be run";
    switch (ks.data?.status) {
      case "ok":
        return `${ksFeatures.length} features`;
      case "no_log":
        return "not enough data — no prediction log yet";
      case "insufficient_data":
        return `not enough data — ${ks.data.observations} of ${MIN_KS_ROWS} rows`;
      default:
        return "no data";
    }
  };

  return (
    <div>
      <div className="eyebrow" style={{ marginBottom: 12 }}>Drift timeline (MLflow): {timelineLabel()}</div>
      {data.length > 0 ? (
      <div className="drift-chart-wrap" style={{ marginBottom: 24 }}>
        <ResponsiveContainer width="100%" height="100%" debounce={50}>
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis dataKey="timeShort" stroke="var(--muted)" fontSize={11} />
            <YAxis domain={[0, 1]} stroke="var(--muted)" fontSize={11} />
            <Tooltip contentStyle={{ background: "var(--bg)", border: "1px solid rgba(255,255,255,0.1)", fontSize: 12 }} />
            <ReferenceLine y={0.31} stroke="#F5C84B" strokeDasharray="4 4" label={{ value: "threshold 0.31", fill: "#F5C84B", fontSize: 10, position: "right" }} />
            <Line type="monotone" dataKey="drift_score" stroke="#2FE0A6" strokeWidth={2} dot={{ r: 4, fill: "#2FE0A6" }} activeDot={{ r: 6 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      ) : (
        <div className="drift-chart-wrap" style={{ marginBottom: 24, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)" }}>
          {tl.isLoading
            ? "Loading timeline…"
            : tl.isError
              ? "MLflow could not be queried"
              : "No drift events yet"}
        </div>
      )}

      <div className="eyebrow" style={{ marginBottom: 12 }}>Kolmogorov-Smirnov per-feature: {ksLabel()}</div>
      <div className="drift-table" style={{ marginBottom: 24 }}>
        <div className="drift-row drift-head" style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1fr" }}>
          <div>Feature</div>
          <div className="tabular">KS statistic</div>
          <div className="tabular">p-value</div>
          <div>Decision</div>
        </div>
        {ksFeatures.map(([name, f]) => {
          const drift = f.drift;
          const sig = Number(f.p_value ?? 1) < 0.01;
          return (
            <div key={name} className="drift-row" style={{ gridTemplateColumns: "1.4fr 1fr 1fr 1fr" }}>
              <div className="mono" style={{ fontSize: 12 }}>{name}</div>
              <div className="tabular">{Number(f.ks_stat ?? 0).toFixed(4)}</div>
              <div className="tabular" style={{ color: sig ? "#EF4444" : "var(--muted)" }}>{Number(f.p_value ?? 1).toFixed(4)}</div>
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