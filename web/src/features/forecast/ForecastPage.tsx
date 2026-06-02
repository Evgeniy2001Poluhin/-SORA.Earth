import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { api } from "@/api/client";

type Hist = { ds: string; y: number };
type Fc = { ds: string; yhat: number; yhat_lower: number; yhat_upper: number };
type ForecastData = {
  history: Hist[]; forecast: Fc[]; model: string; metric: string;
  slope_per_day: number; ci95: number;
};

export default function ForecastPage() {
  const [horizon, setHorizon] = useState(30);
  const { data, isLoading, error } = useQuery({
    queryKey: ["forecast", horizon],
    queryFn: () => api<ForecastData>(`/forecast?horizon=${horizon}&metric=score`),
  });

  if (isLoading) return <div className="map-page"><p className="muted">Loading forecast...</p></div>;
  if (error)     return <div className="map-page"><p className="err">{(error as Error).message}</p></div>;
  if (!data)     return null;

  const rows = [
    ...data.history.map(h => ({ ds: h.ds, actual: h.y })),
    ...data.forecast.map(f => ({ ds: f.ds, yhat: f.yhat, band: [f.yhat_lower, f.yhat_upper] as [number, number] })),
  ];
  const trendUp = data.slope_per_day >= 0;

  return (
    <div className="map-page">
      <header className="map-header">
        <div>
          <h1>ESG Forecast</h1>
          <p className="muted">
            linear-trend, {data.history.length} days history, {horizon}-day projection, 95% CI
          </p>
        </div>
        <div className="map-stats">
          <div className="stat"><b>{trendUp ? "↑" : "↓"} {Math.abs(data.slope_per_day).toFixed(3)}</b><em>score/day</em></div>
          <div className="stat"><b>±{data.ci95.toFixed(1)}</b><em>95% CI</em></div>
          <div className="stat"><b>{data.forecast.at(-1)?.yhat.toFixed(1)}</b><em>+{horizon}d est</em></div>
        </div>
        <div style={{ marginTop: 8, display: "flex", gap: 6 }}>
          {[14, 30, 60, 90].map(h => (
            <button key={h} onClick={() => setHorizon(h)}
              style={{ padding: "5px 12px", borderRadius: 6, cursor: "pointer",
                background: h === horizon ? "#16a34a" : "#1a1d20",
                border: "1px solid #2a2e33", color: "var(--text)" }}>
              {h}d
            </button>
          ))}
        </div>
      </header>

      <div className="map-wrap" style={{ padding: 16 }}>
        <ResponsiveContainer width="100%" height={520}>
          <ComposedChart data={rows} margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2e33" />
            <XAxis dataKey="ds" tick={{ fontSize: 11, fill: "#9aa" }} minTickGap={40} />
            <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: "#9aa" }} />
            <Tooltip contentStyle={{ background: "#15181b", border: "1px solid #2a2e33" }} />
            <Legend />
            <Area dataKey="band" stroke="none" fill="#16a34a" fillOpacity={0.15} name="95% CI" />
            <Line dataKey="actual" stroke="#3b82f6" dot={false} strokeWidth={2} name="history" />
            <Line dataKey="yhat" stroke="#16a34a" strokeDasharray="6 4" dot={false} strokeWidth={2} name="forecast" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
