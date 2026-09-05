import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceDot, ResponsiveContainer } from "recharts";
import { evaluateApi } from "@/api/endpoints/evaluate";
import type { EvaluateRequest, EvaluateProjectRequest } from "@/api/types";

/** The evaluate form and the last scored run, both possibly incomplete. */
type EvalInput = Partial<EvaluateRequest> & { total_score?: number };
type Props = { form: EvalInput; lastRun: EvalInput | null };

/** Sweepable numeric fields of the evaluate request. */
type SweepKey = "budget_usd" | "co2_reduction_tons_per_year" | "social_impact_score";

const PARAMS: Array<{ key: SweepKey; label: string; min: number; max: number; step: number; fmt: (v: number) => string }> = [
  { key:"budget_usd",                    label:"Budget (USD)",   min:50000, max:500000, step:10000, fmt:(v:number)=>`$${(v/1000).toFixed(0)}k` },
  { key:"co2_reduction_tons_per_year",   label:"CO2 (t/yr)",     min:50,    max:800,    step:25,    fmt:(v:number)=>`${v} t` },
  { key:"social_impact_score",           label:"Social (1-10)",  min:1,     max:10,     step:1,     fmt:(v:number)=>`${v}/10` },
];

export function WhatIf({ form, lastRun }: Props) {
  const wi = useMutation({ mutationFn: evaluateApi.whatIf });
  const base = lastRun || form;
  const [paramKey, setParamKey] = useState<SweepKey>("budget_usd");
  const [sweepData, setSweepData] = useState<Array<{x:number; total:number}>>([]);
  const [sweepLoading, setSweepLoading] = useState(false);

  const param = PARAMS.find(p=>p.key===paramKey)!;

  // Tornado on mount/lastRun change — debounced 400ms to avoid request avalanche.
  // lastRun is spread from a possibly-absent previous run, so fall back to the
  // live form. ?? rather than || so a legitimate 0 is not replaced.
  const budget = base?.budget_usd ?? form.budget_usd;
  const co2 = base?.co2_reduction_tons_per_year ?? form.co2_reduction_tons_per_year;
  const social = base?.social_impact_score ?? form.social_impact_score;
  const duration = base?.project_duration_months ?? form.project_duration_months;
  const country = base?.country ?? form.country;
  useEffect(()=>{
    if (!country) return;
    // Rejects undefined, null, NaN and +/-Infinity without coercing.
    if (![budget, co2, social].every(Number.isFinite)) return;
    const id = setTimeout(() => {
      wi.reset();
      // Send only the backend Project fields; the *_usd / *_score form names
      // are frontend-only and would be ignored by the Pydantic model.
      wi.mutate({
        region: country,
        budget: budget as number,
        co2_reduction: co2 as number,
        social_impact: social as number,
        ...(Number.isFinite(duration) ? { duration_months: duration as number } : {}),
      });
    }, 400);
    return () => clearTimeout(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[budget, co2, social, duration, country]);

  // Sweep: 12 points across param range
  const runSweep = async () => {
    setSweepLoading(true);
    const points: Array<{x:number; total:number}> = [];
    const xs: number[] = [];
    for (let i=0; i<=11; i++) xs.push(param.min + (param.max-param.min)*i/11);
    if (!country || ![budget, co2, social, duration].every(Number.isFinite)) {
      setSweepLoading(false);
      return;
    }
    const sweepBase: EvaluateProjectRequest = {
      project_name: base?.project_name ?? "Project",
      country,
      region: country,
      budget_usd: budget as number,
      co2_reduction_tons_per_year: co2 as number,
      social_impact_score: social as number,
      project_duration_months: duration as number,
    };
    for (const x of xs) {
      const override: Partial<EvaluateRequest> = { [param.key]: x };
      const r = await evaluateApi.evaluate({ ...sweepBase, ...override }).catch(()=>null);
      if (r) points.push({ x, total: r.total_score });
    }
    setSweepData(points);
    setSweepLoading(false);
  };

  const tornado = useMemo(()=>{
    const v = wi.data?.variations; if(!v) return [];
    const rows = [
      { key:"Budget +20%",  delta: v.budget?.score_change,        abs: Math.abs(v.budget?.score_change ?? 0) },
      { key:"CO2 +20%",     delta: v.co2_reduction?.score_change, abs: Math.abs(v.co2_reduction?.score_change ?? 0) },
      { key:"Social +1",    delta: v.social_impact?.score_change, abs: Math.abs(v.social_impact?.score_change ?? 0) },
    ];
    // "Every parameter changes the score by exactly zero" is a claim, not an
    // empty state (#236). `{variations:{}}` from the server used to draw all
    // three bars reading +0.00 — a sensitivity result nobody computed. Rows
    // the server did not answer for are dropped, and if none survive the
    // caller shows "Run an evaluation first" instead.
    const answered = rows.filter(r => typeof r.delta === "number");
    return answered
      .map(r => ({ ...r, delta: r.delta as number }))
      .sort((a,b)=>b.abs-a.abs);
  },[wi.data]);

  const maxAbs = Math.max(0.5, ...tornado.map(t=>t.abs));
  const currentX = base?.[paramKey] ?? param.min;

  return (
    <div className="card-body">
      <h2 className="display" style={{fontSize:24,margin:"0 0 4px"}}>What-If Sensitivity</h2>
      <p style={{color:"var(--muted)",fontSize:13,marginBottom:22}}>How total score reacts when you nudge each parameter</p>

      {/* Tornado */}
      <div className="eyebrow" style={{marginBottom:12}}>Sensitivity Tornado</div>
      {wi.isPending && <div style={{color:"var(--faint)",fontSize:13}}>Computing…</div>}
      {!wi.isPending && tornado.length===0 && <div style={{color:"var(--faint)",fontSize:13}}>Run an evaluation first</div>}
      <div className="wi-tornado">
        {tornado.map(t => (
          <div key={t.key} className="wi-row">
            <div className="wi-lbl mono">{t.key}</div>
            <div className="wi-track">
              <div className={"wi-bar "+(t.delta>=0?"pos":"neg")}
                   style={{width:`${(t.abs/maxAbs)*100}%`}}/>
            </div>
            <div className="wi-val tabular">{t.delta>=0?"+":""}{t.delta.toFixed(2)}</div>
          </div>
        ))}
      </div>

      {/* Sweep */}
      <div className="eyebrow" style={{marginTop:32,marginBottom:12}}>Live Sweep</div>
      <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:14}}>
        {PARAMS.map(p => (
          <button key={p.key}
            onClick={()=>{ setParamKey(p.key); setSweepData([]); }}
            className={"ev-tab"+(paramKey===p.key?" active":"")}>
            {p.label}
          </button>
        ))}
        <button className="ev-btn" style={{marginLeft:"auto",padding:"8px 16px",fontSize:12}}
          onClick={runSweep} disabled={sweepLoading || !base?.country}>
          {sweepLoading ? "Sweeping…" : "Run sweep"}
        </button>
      </div>
      {sweepData.length>0 && (
        <div style={{height:260,marginTop:8}}>
          <ResponsiveContainer width="100%" height="100%" minWidth={1} minHeight={1}>
            <LineChart data={sweepData} margin={{top:8,right:16,left:0,bottom:8}}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)"/>
              <XAxis dataKey="x" tickFormatter={param.fmt} stroke="#666" fontSize={11}/>
              <YAxis domain={[0,100]} stroke="#666" fontSize={11}/>
              <Tooltip
                contentStyle={{background:"var(--bg-1)",border:"1px solid var(--line-2)",borderRadius:8,fontSize:12}}
                formatter={(v: unknown)=>[`${Number(v).toFixed(1)}`,"Total"]}
                labelFormatter={(v: unknown)=>`${param.label}: ${param.fmt(Number(v))}`}/>
              <Line type="monotone" dataKey="total" stroke="#2FE0A6" strokeWidth={2} dot={{r:3,fill:"#2FE0A6"}} animationDuration={400}/>
              <ReferenceDot x={currentX} y={base?.total_score ?? 50} r={6} fill="#fff" stroke="#2FE0A6" strokeWidth={2}/>
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
