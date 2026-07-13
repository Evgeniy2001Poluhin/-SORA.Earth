import { useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { api } from "@/api/client";

type HistPoint = { ds: string; y: number };
type FcPoint = { ds: string; yhat: number; yhat_lower: number; yhat_upper: number };
type ForecastResponse = {
  history: HistPoint[];
  forecast: FcPoint[];
  model: string;
  metric: string;
  confidence?: "high" | "medium" | "low";
  metadata?: Record<string, any>;
};

type MetricsResponse = {
  [metric: string]: {
    [model: string]: {
      trained_at: string;
      mae: number;
      rmse: number;
      mape: number;
      r2_score: number;
      train_samples: number;
      test_samples: number;
      training_duration_sec: number;
    };
  };
};

const MODELS = ["ensemble", "prophet", "lstm"] as const;
const METRICS = [
  { value: "score", label: "ESG Score" },
  { value: "prob", label: "Success Prob" },
  { value: "co2_reduction", label: "CO₂ Reduction" },
] as const;

const MODEL_COLORS: Record<string, string> = {
  ensemble: "#3b82f6",  // blue
  prophet: "#10b981",   // green
  lstm: "#f59e0b",      // amber
};

export default function ForecastComparePage() {
  const [horizon, setHorizon] = useState(30);
  const [metric, setMetric] = useState<string>("score");
  const [showBands, setShowBands] = useState(true);

  // Fetch all 3 models in parallel
  const queries = useQueries({
    queries: MODELS.map(model => ({
      queryKey: ["forecast", horizon, model, metric],
      queryFn: () =>
        api<ForecastResponse>(`/forecast?horizon=${horizon}&metric=${metric}&model=${model}`),
      staleTime: 5 * 60 * 1000, // 5 minutes
    })),
  });

  // Fetch performance metrics
  const { data: metricsData } = useQuery({
    queryKey: ["forecast-metrics"],
    queryFn: () => api<MetricsResponse>("/forecast/metrics/latest"),
    staleTime: 10 * 60 * 1000, // 10 minutes
  });

  const isLoading = queries.some(q => q.isLoading);
  const hasError = queries.some(q => q.error);

  // Combine data from all models
  const combinedData: Array<{
    ds: string;
    actual?: number;
    ensemble_yhat?: number;
    prophet_yhat?: number;
    lstm_yhat?: number;
    ensemble_band?: [number, number];
    prophet_band?: [number, number];
    lstm_band?: [number, number];
  }> = [];

  if (queries[0].data) {
    // History (same for all models)
    queries[0].data.history.forEach(h => {
      combinedData.push({ ds: h.ds, actual: h.y });
    });

    // Collect all unique forecast dates from all models
    const allForecastDates = new Set<string>();
    queries.forEach(q => {
      q.data?.forecast.forEach(f => allForecastDates.add(f.ds));
    });

    // Create rows for all forecast dates
    Array.from(allForecastDates).sort().forEach(ds => {
      combinedData.push({ ds });
    });

    // Populate forecasts by matching dates (not indices!)
    queries.forEach((q, idx) => {
      const model = MODELS[idx];
      q.data?.forecast.forEach(f => {
        const row = combinedData.find(r => r.ds === f.ds);
        if (row) {
          row[`${model}_yhat`] = f.yhat;
          row[`${model}_band`] = [f.yhat_lower, f.yhat_upper];
        }
      });
    });
  }

  const lastHistoryDate = queries[0].data?.history.at(-1)?.ds;
  const yDomain = metric === "prob" ? [0, 1] : metric === "score" ? [0, 100] : undefined;

  // Get current metric performance
  const currentMetrics = metricsData?.[metric];

  return (
    <div className="map-page">
      <header className="map-header">
        <div>
          <h1>Forecast Model Comparison</h1>
          <p className="muted">
            Compare {MODELS.length} models side-by-side
            {queries[0].data && <> · {queries[0].data.history.length}d history · {horizon}d projection</>}
          </p>
        </div>
      </header>

      {/* Controls */}
      <div style={{ padding: "0 24px", display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
        {/* Horizon */}
        <div style={{ display: "flex", gap: 4 }}>
          {[14, 30, 60, 90].map(h => (
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

        {/* Toggle CI bands */}
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, cursor: "pointer" }}>
          <input type="checkbox" checked={showBands} onChange={e => setShowBands(e.target.checked)} />
          Show confidence intervals
        </label>
      </div>

      {/* Chart */}
      <div className="map-wrap" style={{ padding: 16 }}>
        {isLoading && <p className="muted" style={{ textAlign: "center", padding: 40 }}>Loading forecasts…</p>}
        {hasError && <p className="err" style={{ textAlign: "center", padding: 40 }}>Failed to load forecasts</p>}
        {!isLoading && !hasError && combinedData.length > 0 && (
          <ResponsiveContainer width="100%" height={520}>
            <ComposedChart data={combinedData} margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
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
                formatter={(value: any, name: any) => {
                  if (Array.isArray(value))
                    return [`${value[0].toFixed(2)} — ${value[1].toFixed(2)}`, name];
                  if (typeof value === "number") return [value.toFixed(3), name];
                  return [value, name];
                }}
              />
              <Legend />
              {lastHistoryDate && (
                <ReferenceLine
                  x={lastHistoryDate}
                  stroke="#555"
                  strokeDasharray="4 4"
                  label={{ value: "now", fill: "#777", fontSize: 10 }}
                />
              )}

              {/* Confidence bands */}
              {showBands && (
                <>
                  <Area
                    dataKey="ensemble_band"
                    stroke="none"
                    fill={MODEL_COLORS.ensemble}
                    fillOpacity={0.1}
                    name="Ensemble CI"
                    connectNulls={false}
                  />
                  <Area
                    dataKey="prophet_band"
                    stroke="none"
                    fill={MODEL_COLORS.prophet}
                    fillOpacity={0.1}
                    name="Prophet CI"
                    connectNulls={false}
                  />
                  <Area
                    dataKey="lstm_band"
                    stroke="none"
                    fill={MODEL_COLORS.lstm}
                    fillOpacity={0.1}
                    name="LSTM CI"
                    connectNulls={false}
                  />
                </>
              )}

              {/* History */}
              <Line
                dataKey="actual"
                stroke="#888"
                dot={false}
                strokeWidth={2}
                name="History"
                connectNulls={false}
              />

              {/* Forecasts */}
              <Line
                dataKey="ensemble_yhat"
                stroke={MODEL_COLORS.ensemble}
                strokeDasharray="6 4"
                dot={false}
                strokeWidth={2}
                name="Ensemble"
                connectNulls={false}
              />
              <Line
                dataKey="prophet_yhat"
                stroke={MODEL_COLORS.prophet}
                strokeDasharray="6 4"
                dot={false}
                strokeWidth={2}
                name="Prophet"
                connectNulls={false}
              />
              <Line
                dataKey="lstm_yhat"
                stroke={MODEL_COLORS.lstm}
                strokeDasharray="6 4"
                dot={false}
                strokeWidth={2}
                name="LSTM"
                connectNulls={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Performance Metrics */}
      {currentMetrics && (
        <div style={{ padding: "0 24px 24px" }}>
          <h3 style={{ marginBottom: 12, fontSize: 14, color: "#aaa" }}>
            Model Performance (Walk-Forward Validation)
          </h3>
          <div style={{ overflowX: "auto" }}>
            <table style={{
              width: "100%",
              fontSize: 12,
              borderCollapse: "collapse",
              background: "#1a1d20",
              border: "1px solid #2a2e33",
              borderRadius: 8,
            }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #2a2e33" }}>
                  <th style={{ padding: "8px 12px", textAlign: "left", color: "#aaa" }}>Model</th>
                  <th style={{ padding: "8px 12px", textAlign: "right", color: "#aaa" }}>MAE</th>
                  <th style={{ padding: "8px 12px", textAlign: "right", color: "#aaa" }}>RMSE</th>
                  <th style={{ padding: "8px 12px", textAlign: "right", color: "#aaa" }}>MAPE (%)</th>
                  <th style={{ padding: "8px 12px", textAlign: "right", color: "#aaa" }}>R²</th>
                  <th style={{ padding: "8px 12px", textAlign: "right", color: "#aaa" }}>Train / Test</th>
                  <th style={{ padding: "8px 12px", textAlign: "right", color: "#aaa" }}>Updated</th>
                </tr>
              </thead>
              <tbody>
                {MODELS.filter(model => currentMetrics[model]).map(model => {
                  const m = currentMetrics[model];
                  return (
                    <tr key={model} style={{ borderTop: "1px solid #2a2e33" }}>
                      <td style={{
                        padding: "8px 12px",
                        color: MODEL_COLORS[model],
                        fontWeight: 600,
                        textTransform: "capitalize",
                      }}>
                        {model}
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right", color: "#ccc" }}>
                        {m.mae.toFixed(2)}
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right", color: "#ccc" }}>
                        {m.rmse.toFixed(2)}
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right", color: "#ccc" }}>
                        {m.mape.toFixed(2)}%
                      </td>
                      <td style={{
                        padding: "8px 12px",
                        textAlign: "right",
                        color: m.r2_score < 0 ? "#ef4444" : "#10b981",
                        fontWeight: m.r2_score < 0 ? 600 : 400,
                      }}>
                        {m.r2_score.toFixed(2)}
                        {m.r2_score < 0 && (
                          <span style={{ marginLeft: 4, fontSize: 10, opacity: 0.7 }}>⚠️</span>
                        )}
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right", color: "#888", fontSize: 11 }}>
                        {m.train_samples} / {m.test_samples ?? 0}
                      </td>
                      <td style={{ padding: "8px 12px", textAlign: "right", color: "#888", fontSize: 11 }}>
                        {new Date(m.trained_at).toLocaleDateString()}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {Object.values(currentMetrics).some(m => m.r2_score < 0) && (
            <p style={{ marginTop: 12, fontSize: 11, color: "#888", fontStyle: "italic" }}>
              ⚠️ Negative R² indicates insufficient test data ({currentMetrics.ensemble?.test_samples ?? 0} samples).
              Performance metrics will stabilize with 30+ test samples (~60 days of data).
            </p>
          )}
        </div>
      )}
    </div>
  );
}
