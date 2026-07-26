import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { api } from "@/api/client";

/** Model-specific extras returned alongside a forecast; see app/services/forecasting. */
type ForecastMetadata = {
  weights?: Record<string, number>;
  models_used?: string[];
  total_weight?: number;
  mc_samples?: number;
  avg_ci_width?: number;
  slope_per_day?: number;
  autoregressive?: boolean;
};

type HistPoint = { ds: string; y: number };
type FcPoint = { ds: string; yhat: number; yhat_lower: number; yhat_upper: number };
type ForecastResponse = {
  history: HistPoint[];
  forecast: FcPoint[];
  model: string;
  metric: string;
  confidence?: "high" | "medium" | "low";
  metadata?: ForecastMetadata;
};

const MODELS = ["ensemble", "prophet", "lstm", "linear"] as const;
const METRICS = [
  { value: "score", label: "ESG Score" },
  { value: "prob", label: "Success Prob" },
  { value: "co2_reduction", label: "CO₂ Reduction" },
] as const;

const CONFIDENCE_COLOR: Record<string, string> = {
  high: "#2FE0A6",
  medium: "#F5C84B",
  low: "#EF4444",
};

export default function ForecastPage() {
  const [horizon, setHorizon] = useState(30);
  const [model, setModel] = useState<string>("ensemble");
  const [metric, setMetric] = useState<string>("score");

  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: ["forecast", horizon, model, metric],
    queryFn: () =>
      api<ForecastResponse>(`/forecast?horizon=${horizon}&metric=${metric}&model=${model}`),
  });

  const rows = data ? [
    ...data.history.map(h => ({
      ds: h.ds, actual: h.y, yhat: undefined as number | undefined,
      band: undefined as [number, number] | undefined,
    })),
    ...data.forecast.map(f => ({
      ds: f.ds, actual: undefined as number | undefined,
      yhat: f.yhat, band: [f.yhat_lower, f.yhat_upper] as [number, number],
    })),
  ] : [];

  const lastActual = data?.history.at(-1)?.y;
  const lastForecast = data?.forecast.at(-1)?.yhat;
  const delta = lastActual && lastForecast ? lastForecast - lastActual : null;
  const confidence = data?.confidence ?? "low";
  const yDomain = metric === "prob" ? [0, 1] : metric === "score" ? [0, 100] : undefined;

  return (
    <div className="map-page">
      <header className="map-header">
        <div>
          <h1>Forecast</h1>
          <p className="muted">
            {data?.model ?? "—"} model
            {data && <> · {data.history.length}d history · {horizon}d projection</>}
            {isFetching && !isLoading && <span style={{ marginLeft: 8, opacity: 0.5 }}>updating…</span>}
          </p>
        </div>

        {data && (
          <div className="map-stats">
            <div className="stat">
              <b style={{ color: CONFIDENCE_COLOR[confidence] }}>
                {confidence.toUpperCase()}
              </b>
              <em>confidence</em>
            </div>
            {delta !== null && (
              <div className="stat">
                <b>{delta >= 0 ? "+" : ""}{delta.toFixed(2)}</b>
                <em>Δ {horizon}d</em>
              </div>
            )}
            {lastForecast !== undefined && (
              <div className="stat">
                <b>{lastForecast.toFixed(1)}</b>
                <em>+{horizon}d est</em>
              </div>
            )}
            {data.metadata?.weights && (
              <div className="stat">
                <b>L:{(data.metadata.weights.lstm * 100).toFixed(0)}% P:{(data.metadata.weights.prophet * 100).toFixed(0)}%</b>
                <em>weights</em>
              </div>
            )}
          </div>
        )}
      </header>

      {/* Controls */}
      <div style={{ padding: "0 24px", display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
        {/* Horizon */}
        <div style={{ display: "flex", gap: 4 }}>
          {[14, 30, 60, 90, 180].map(h => (
            <button key={h} onClick={() => setHorizon(h)}
              style={{
                padding: "5px 12px", borderRadius: 6, cursor: "pointer",
                background: h === horizon ? "#16a34a" : "#1a1d20",
                border: "1px solid #2a2e33", color: "var(--text)", fontSize: 12,
              }}>
              {h}d
            </button>
          ))}
        </div>

        {/* Model */}
        <div style={{ display: "flex", gap: 4 }}>
          {MODELS.map(m => (
            <button key={m} onClick={() => setModel(m)}
              style={{
                padding: "5px 12px", borderRadius: 6, cursor: "pointer",
                background: m === model ? "#3b82f6" : "#1a1d20",
                border: "1px solid #2a2e33", color: "var(--text)", fontSize: 12,
                textTransform: "capitalize",
              }}>
              {m}
            </button>
          ))}
        </div>

        {/* Metric */}
        <div style={{ display: "flex", gap: 4 }}>
          {METRICS.map(mt => (
            <button key={mt.value} onClick={() => setMetric(mt.value)}
              style={{
                padding: "5px 12px", borderRadius: 6, cursor: "pointer",
                background: mt.value === metric ? "#8b5cf6" : "#1a1d20",
                border: "1px solid #2a2e33", color: "var(--text)", fontSize: 12,
              }}>
              {mt.label}
            </button>
          ))}
        </div>
        </div>

        {/* Compare Models Link */}
        <Link to="/forecast/compare" style={{
          padding: "6px 14px",
          borderRadius: 6,
          background: "#16a34a",
          color: "white",
          textDecoration: "none",
          fontSize: 12,
          fontWeight: 600,
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          border: "none",
        }}>
          <span>⚡</span>
          Compare Models
        </Link>
      </div>

      {/* Chart */}
      <div className="map-wrap" style={{ padding: 16 }}>
        {isLoading && <p className="muted" style={{ textAlign: "center", padding: 40 }}>Loading forecast…</p>}
        {error && <p className="err" style={{ textAlign: "center", padding: 40 }}>{(error as Error).message}</p>}
        {data && data.forecast.length === 0 && (
          <p className="muted" style={{ textAlign: "center", padding: 40 }}>
            Not enough historical data for forecasting. At least 3 evaluations needed.
          </p>
        )}
        {data && data.forecast.length > 0 && (
          <ResponsiveContainer width="100%" height={520}>
            <ComposedChart data={rows} margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2e33" />
              <XAxis
                dataKey="ds"
                tick={{ fontSize: 11, fill: "#9aa" }}
                minTickGap={50}
                tickFormatter={(v: string) => v.slice(5)}
              />
              <YAxis
                domain={yDomain}
                tick={{ fontSize: 11, fill: "#9aa" }}
                tickFormatter={(v: number) => metric === "prob" ? `${(v * 100).toFixed(0)}%` : String(v)}
              />
              <Tooltip
                contentStyle={{ background: "#15181b", border: "1px solid #2a2e33", borderRadius: 8 }}
                labelFormatter={(l) => `Date: ${l}`}
                formatter={(value: unknown, name: unknown) => {
                  const label = String(name ?? "");
                  if (label === "90% CI" && Array.isArray(value))
                    return [`${Number(value[0]).toFixed(2)} — ${Number(value[1]).toFixed(2)}`, label];
                  if (typeof value === "number") return [value.toFixed(3), label];
                  return [String(value ?? ""), label];
                }}
              />
              <Legend />
              {data.history.length > 0 && (
                <ReferenceLine
                  x={data.history.at(-1)!.ds}
                  stroke="#555"
                  strokeDasharray="4 4"
                  label={{ value: "now", fill: "#777", fontSize: 10 }}
                />
              )}
              <Area
                dataKey="band"
                stroke="none"
                fill="#16a34a"
                fillOpacity={0.12}
                name="90% CI"
                connectNulls={false}
              />
              <Line
                dataKey="actual"
                stroke="#3b82f6"
                dot={false}
                strokeWidth={2}
                name="History"
                connectNulls={false}
              />
              <Line
                dataKey="yhat"
                stroke="#16a34a"
                strokeDasharray="6 4"
                dot={false}
                strokeWidth={2}
                name="Forecast"
                connectNulls={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Metadata */}
      {data?.metadata && (
        <div style={{ padding: "0 24px 16px", fontSize: 12, color: "#888" }}>
          {data.metadata.mc_samples && <span>MC samples: {data.metadata.mc_samples} · </span>}
          {data.metadata.avg_ci_width != null && <span>Avg CI: ±{data.metadata.avg_ci_width.toFixed(3)} · </span>}
          {data.metadata.slope_per_day != null && <span>Slope: {data.metadata.slope_per_day.toFixed(4)}/day · </span>}
          {data.metadata.models_used && <span>Models: {data.metadata.models_used.join(", ")} · </span>}
          {data.metadata.autoregressive && <span>Autoregressive: ✓</span>}
        </div>
      )}
    </div>
  );
}
