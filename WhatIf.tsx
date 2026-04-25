import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceDot, ResponsiveContainer } from "recharts";
import { evaluateApi } from "@/api/endpoints/evaluate";

type Props = { form: any; lastRun: any };

const PARAMS = [
  { key: "budget_usd",                  label: "Budget (USD)",  min: 50000, max: 500000, step: 10000, fmt: (v: number) => `$${(v / 1000).toFixed(0)}k` },
  { key: "co2_reduction_tons_per_year", label: "CO\u2082 (t/yr)",   min: 50,    max: 800,    step: 25,    fmt: (v: number) => `${v} t` },
  { key: "social_impact_score",         label: "Social (1-10)", min: 1,     max: 10,     step: 1,     fmt: (v: number) => `${v}/10` },
];

export function WhatIf({ form, lastRun }: Props) {
  const wi = useMutation({ mutationFn: evaluateApi.whatIf });
  const base = lastRun || form;
  const [paramKey, setParamKey] = useState("budget_usd");
  const [sweepData, setSweepData] = useState<Array<{ x: number; total: number }>>([]);
  const [sweepLoading, setSweepLoading] = useState(false);

  const param = PARAMS.find(p => p.key === paramKey)!;

  useEffect(() => {
    if (!base?.country) return;
    wi.mutate({
      ...base,
      region: base.country,
      name: base.project_name || form.project_name,
      budget: base.budget_usd,
      co2_reduction: base.co2_reduction_tons_per_year,
      social_impact: base.social_impact_score,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base?.budget_usd, base?.country]);

  const runSweep = async () => {
    setSweepLoading(true);
    const xs: number[] = [];
    for (let i = 0; i <= 11; i++) xs.push(param.min + (param.max - param.min) * i / 11);
    const results = await Promise.all(
      xs.map(x => evaluateApi.evaluate({ ...base, region: base.country, [param.key]: x } as any).catch(() => null))
    );
    const points: Array<{ x: number; total: number }> = [];
    results.forEach((r, i) => { if (r) points.push({ x: xs[i], total: (r as any).total_score }); });
    setSweepData(points);
    setSweepLoading(false);
  };

  const tornado = useMemo(() => {
    const v = wi.data?.variations;
    if (!v) return [];
    return [
      { key: "Budget +20%", delta: v.budget?.score_change ?? 0,        abs: Math.abs(v.budget?.score_change ?? 0) },
      { key: "CO\u2082 +20%",    delta: v.co2_reduction?.score_change ?? 0, abs: Math.abs(v.co2_reduction?.score_change ?? 0) },
      { key: "Social +1",   delta: v.social_impact?.score_change ?? 0, abs: Math.abs(v.social_impact?.score_change ?? 0) },
    ].sort((a, b) => b.abs - a.abs);
  }, [wi.data]);

  const maxAbs = Math.max(0.5, ...tornado.map(t => t.abs));
  const currentX = base?.[paramKey] ?? param.min;
  const currentY = base?.total_score ?? 50;

  return (
    <div className="card-body">
      <h2 className="display" style={{ fontSize: 24, margin: "0 0 4px" }}>What-If Sensitivity</h2>
      <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 22 }}>How total score reacts when you nudge each parameter</p>

      <div className="eyebrow" style={{ marginBottom: 12 }}>Sensitivity Tornado</div>
      {wi.isPending && <div style={{ color: "var(--faint)", fontSize: 13 }}>Computing...</div>}
      {!wi.isPending && tornado.length === 0 && <div style={{ color: "var(--faint)", fontSize: 13 }}>Run an evaluation first</div>}
      <div className="wi-tornado">
        {tornado.map(t => (
          <div key={t.key} className="wi-row">
            <div className="wi-lbl mono">{t.key}</div>
            <div className="wi-track">
              <div className={"wi-bar " + (t.delta >= 0 ? "pos" : "neg")} style={{ width: `${(t.abs / maxAbs) * 100}%` }} />
            </div>
            <div className="wi-val tabular">{t.delta >= 0 ? "+" : ""}{t.delta.toFixed(2)}</div>
          </div>
        ))}
      </div>

      <div className="eyebrow" style={{ marginTop: 32, marginBottom: 12 }}>Live Sweep</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        {PARAMS.map(p => (
          <button key={p.key} onClick={() => { setParamKey(p.key); setSweepData([]); }} className={"ev-tab" + (paramKey === p.key ? " active" : "")}>
            {p.label}
          </button>
        ))}
        <button className="ev-btn" style={{ marginLeft: "auto", padding: "8px 16px", fontSize: 12 }} onClick={runSweep} disabled={sweepLoading || !base?.country}>
          {sweepLoading ? "Sweeping..." : "Run sweep"}
        </button>
      </div>

      {sweepData.length > 0 && (
        <div style={{ height: 260, marginTop: 8 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sweepData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="x" tickFormatter={param.fmt} stroke="#666" fontSize={11} />
              <YAxis domain={[0, 100]} stroke="#666" fontSize={11} />
              <Tooltip
                contentStyle={{ background: "#0E1218", border: "1px solid #1A2230", borderRadius: 8, fontSize: 12 }}
                formatter={(v: any) => [`${Number(v).toFixed(1)}`, "Total"]}
                labelFormatter={(v: any) => `${param.label}: ${param.fmt(Number(v))}`}
              />
              <Line type="monotone" dataKey="total" stroke="#2FE0A6" strokeWidth={2} dot={{ r: 3, fill: "#2FE0A6" }} animationDuration={400} />
              <ReferenceDot x={currentX} y={currentY} r={6} fill="#fff" stroke="#2FE0A6" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
