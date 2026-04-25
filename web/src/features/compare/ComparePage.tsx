import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { evaluateApi } from "@/api/endpoints/evaluate";
import type { EvaluateRequest, EvaluateResponse } from "@/api/types";
import "./compare.css";

const PRESETS: { id: string; label: string; body: EvaluateRequest }[] = [
  { id: "solar",  label: "Solar",        body: { project_name: "Solar Farm",   country: "Sweden",  budget_usd: 150000, co2_reduction_tons_per_year: 120, social_impact_score: 9, project_duration_months: 18 } },
  { id: "wind",   label: "Wind",         body: { project_name: "Offshore Wind", country: "Germany", budget_usd: 280000, co2_reduction_tons_per_year: 310, social_impact_score: 8, project_duration_months: 24 } },
  { id: "refo",   label: "Reforest",     body: { project_name: "Boreal",       country: "Canada",  budget_usd: 80000,  co2_reduction_tons_per_year: 200, social_impact_score: 7, project_duration_months: 36 } },
  { id: "water",  label: "Water",        body: { project_name: "Water Grid",   country: "Spain",   budget_usd: 120000, co2_reduction_tons_per_year: 60,  social_impact_score: 9, project_duration_months: 20 } },
];

type Side = "A" | "B";

function Field(props: { label: string; value: number; onChange: (v: number) => void; step?: number; min?: number; max?: number }) {
  return (
    <label className="cmp-field">
      <span>{props.label}</span>
      <input type="number" value={props.value} step={props.step ?? 1} min={props.min} max={props.max}
        onChange={e => props.onChange(Number(e.target.value))} />
    </label>
  );
}

function PresetRow(props: { active: string; onPick: (b: EvaluateRequest, id: string) => void }) {
  return (
    <div className="cmp-presets">
      {PRESETS.map(p => (
        <button key={p.id} className={"cmp-chip" + (p.id === props.active ? " active" : "")}
          onClick={() => props.onPick(p.body, p.id)}>{p.label}</button>
      ))}
    </div>
  );
}

function Bar(props: { label: string; v: number; tone: string }) {
  const w = Math.max(0, Math.min(100, props.v));
  return (
    <div className="cmp-bar">
      <div className="cmp-bar-head"><span>{props.label}</span><span className="tabular">{props.v.toFixed(1)}</span></div>
      <div className="cmp-bar-track"><span className={"cmp-bar-fill " + props.tone} style={{ width: w + "%" }} /></div>
    </div>
  );
}

function ResultCard(props: { side: Side; result: EvaluateResponse | undefined; loading: boolean }) {
  const r = props.result;
  if (props.loading) return <div className="cmp-result loading">Running…</div>;
  if (!r) return <div className="cmp-result empty">Press Run to evaluate.</div>;
  const tone = r.risk_level === "Low" ? "low" : r.risk_level === "High" ? "high" : "med";
  return (
    <div className="cmp-result">
      <div className="cmp-score-row">
        <div className="cmp-score tabular">{r.total_score.toFixed(1)}</div>
        <span className={"cmp-risk " + tone}>{r.risk_level} risk</span>
      </div>
      <div className="cmp-score-sub">/ 100 · ESG SCORE · success {r.success_probability.toFixed(1)}%</div>
      <Bar label="Environment" v={r.environment_score} tone="env" />
      <Bar label="Social"      v={r.social_score}      tone="soc" />
      <Bar label="Economic"    v={r.economic_score}    tone="eco" />
    </div>
  );
}

function useSide(initial: EvaluateRequest, presetId: string) {
  const [form, setForm] = useState<EvaluateRequest>(initial);
  const [active, setActive] = useState(presetId);
  const mut = useMutation({ mutationFn: evaluateApi.evaluate });
  return { form, setForm, active, setActive, mut };
}

export function ComparePage() {
  const A = useSide(PRESETS[0].body, "solar");
  const B = useSide(PRESETS[1].body, "wind");

  const runBoth = () => {
    const payloadA = { ...A.form, region: A.form.country } as any;
    const payloadB = { ...B.form, region: B.form.country } as any;
    A.mut.mutate(payloadA);
    B.mut.mutate(payloadB);
  };
  useEffect(() => { runBoth(); /* eslint-disable-next-line */ }, []);

  const ra = A.mut.data, rb = B.mut.data;
  const diff = useMemo(() => {
    if (!ra || !rb) return null;
    return {
      total: rb.total_score - ra.total_score,
      env: rb.environment_score - ra.environment_score,
      soc: rb.social_score - ra.social_score,
      eco: rb.economic_score - ra.economic_score,
    };
  }, [ra, rb]);

  const renderColumn = (side: Side, S: ReturnType<typeof useSide>, title: string) => (
    <section className="card cmp-col">
      <header className="cmp-col-head">
        <span className="eyebrow">{title}</span>
        <input className="cmp-name" value={S.form.project_name} onChange={e => S.setForm({ ...S.form, project_name: e.target.value })} />
      </header>
      <PresetRow active={S.active} onPick={(b, id) => { S.setForm(b); S.setActive(id); }} />
      <div className="cmp-fields">
        <label className="cmp-field"><span>Country</span>
          <input value={S.form.country} onChange={e => S.setForm({ ...S.form, country: e.target.value })} />
        </label>
        <Field label="Budget (USD)" value={S.form.budget_usd}                  onChange={v => S.setForm({ ...S.form, budget_usd: v })} step={10000} />
        <Field label="CO2 (t/yr)"   value={S.form.co2_reduction_tons_per_year} onChange={v => S.setForm({ ...S.form, co2_reduction_tons_per_year: v })} />
        <Field label="Social (1-10)" value={S.form.social_impact_score}        onChange={v => S.setForm({ ...S.form, social_impact_score: v })} min={1} max={10} />
        <Field label="Duration (mo)" value={S.form.project_duration_months}    onChange={v => S.setForm({ ...S.form, project_duration_months: v })} min={1} />
      </div>
      <ResultCard side={side} result={S.mut.data} loading={S.mut.isPending} />
    </section>
  );

  const winner = diff && diff.total !== 0 ? (diff.total > 0 ? "B" : "A") : null;
  const dsign = (n: number) => (n >= 0 ? "+" + n.toFixed(1) : n.toFixed(1));

  return (
    <div className="cmp-page">
      <div className="cmp-hero">
        <h1 className="display">Compare two ESG projects.</h1>
        <p>Run them side-by-side, see the gap on every axis. Same model, same benchmarks, no PR spin.</p>
        <button className="btn-primary" onClick={runBoth}>Run both</button>
      </div>

      <div className="cmp-grid">
        {renderColumn("A", A, "PROJECT A")}
        {renderColumn("B", B, "PROJECT B")}
      </div>

      {diff && (
        <div className={"cmp-diff " + (winner ? "winner-" + winner : "")}>
          <div className="cmp-diff-head">
            <span className="eyebrow">DIFFERENCE</span>
            {winner ? (
              <span>{winner === "A" ? A.form.project_name : B.form.project_name} leads by Δ {dsign(Math.abs(diff.total))}</span>
            ) : <span>Tie</span>}
          </div>
          <div className="cmp-diff-axes">
            <div><span>Environment</span><b className={diff.env >= 0 ? "pos" : "neg"}>{dsign(diff.env)}</b></div>
            <div><span>Social</span><b className={diff.soc >= 0 ? "ps" : "neg"}>{dsign(diff.soc)}</b></div>
            <div><span>Economic</span><b className={diff.eco >= 0 ? "pos" : "neg"}>{dsign(diff.eco)}</b></div>
            <div><span>Total</span><b className={diff.total >= 0 ? "pos" : "neg"}>{dsign(diff.total)}</b></div>
          </div>
          <div className="cmp-diff-note">Positive Δ favours Project B over Project A.</div>
        </div>
      )}
    </div>
  );
}
