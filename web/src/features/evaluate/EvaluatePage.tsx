import { reportApi } from "../../api/endpoints/report";
import Combobox from "../../components/Combobox";
import CountryRanking from "./CountryRanking";
import MonteCarlo from "./MonteCarlo";
import { WhatIf } from "./WhatIf";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { historyApi } from "@/api/endpoints/history";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import { evaluateApi, SaveNotConfirmedError } from "@/api/endpoints/evaluate";
import type { EvaluateRequest, EvaluateResponse, ExplainResponse, RiskLevel } from "@/api/types";
import { ApiError } from "@/api/client";
import { useAuth } from "@/store/auth";
import { saveAccess } from "@/app/access";
import { isSessionEnded } from "@/lib/session";
import { UncertaintyCard } from "./UncertaintyCard";
import { SUCCESS_PROBABILITY_LABEL, SUCCESS_PROBABILITY_NOTE } from "./successProbabilityNote";
import { useServiceSnapshot } from "./useServiceHealth";
import { HealthPill } from "./HealthPill";
import { fmtNum, fmtMoney, clamp } from "@/lib/format";
import "./evaluate.css";
import { errorMessage } from "@/lib/errors";
import type { HistoryItem } from "@/api/endpoints/history";

const PRESETS: { id:string; label:string; body:EvaluateRequest }[] = [
  { id:"solar",  label:"Solar Energy",     body:{ project_name:"Solar Farm",           country:"Sweden",  budget_usd:150000, co2_reduction_tons_per_year:340, social_impact_score:9, project_duration_months:18 } },
  { id:"wind",   label:"Wind Farm",        body:{ project_name:"Offshore Wind",        country:"Germany", budget_usd:280000, co2_reduction_tons_per_year:620, social_impact_score:8, project_duration_months:24 } },
  { id:"refo",   label:"Reforestation",    body:{ project_name:"Boreal Reforestation", country:"Canada",  budget_usd:80000,  co2_reduction_tons_per_year:280, social_impact_score:7, project_duration_months:36 } },
  { id:"water",  label:"Water Treatment",  body:{ project_name:"Urban Water Grid",     country:"Spain",   budget_usd:120000, co2_reduction_tons_per_year:180, social_impact_score:9, project_duration_months:20 } },
  { id:"ev",     label:"EV Infrastructure",body:{ project_name:"Fast-Charge Network",  country:"France",  budget_usd:450000, co2_reduction_tons_per_year:780, social_impact_score:7, project_duration_months:28 } },
  { id:"waste",  label:"Waste Recycling",  body:{ project_name:"Circular Materials",   country:"Italy",   budget_usd:95000,  co2_reduction_tons_per_year:240, social_impact_score:8, project_duration_months:16 } },
];
const riskTone: Record<RiskLevel,string> = { Low:"low", Medium:"med", High:"high" };

function useCountUp(target:number, duration=900) {
  const [v, setV] = useState(0);
  useEffect(()=>{ let raf=0; const start=performance.now();
    const tick=(t:number)=>{ const k=Math.min(1,(t-start)/duration); const e=1-Math.pow(1-k,3);
      setV(target*e); if(k<1) raf=requestAnimationFrame(tick); };
    raf=requestAnimationFrame(tick); return ()=>cancelAnimationFrame(raf);
  },[target,duration]); return v;
}

/** What one Calculate or Calculate-and-save produced: the inputs actually
 *  submitted, the answer to that very call, and whether the server confirmed a
 *  save. The result card, What-If and the PDF read these, never the form, which
 *  the user may have edited since. */
type SaveState = { kind: "none" } | { kind: "saved"; id: number };
interface Run { inputs: EvaluateRequest & { region: string }; result: EvaluateResponse; save: SaveState }

/** Why a save cannot be shown as saved. No id, and no claim either way unless the server made one. */
function saveFailureText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return "Sign in to save evaluations.";
    if (e.status === 403) return "Saving is not available for your role.";
    if (e.status === 422) return "The evaluation was not saved: the server refused the inputs.";
  }
  // A dropped connection, a 5xx (possibly from a proxy after the commit), or an
  // answer short of 201 + saved + id: the row may exist. Say so, and retry nothing.
  const detail = e instanceof SaveNotConfirmedError ? " (" + e.message + ")" : "";
  return "Save not confirmed. Check your history before saving again." + detail;
}

export function EvaluatePage() {
  const { data: countries } = useQuery({ queryKey:["countries"], queryFn: evaluateApi.countries });
  const qc = useQueryClient();
  const user = useAuth((s) => s.user);
  const phase = useAuth((s) => s.phase);
  const epoch = useAuth((s) => s.epoch);
  const canSave = saveAccess(user);
  const [form, setForm] = useState<EvaluateRequest>(PRESETS[0].body);
  const [activePreset, setActivePreset] = useState("solar");
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState<null | "calculate" | "save">(null);
  const [saveProblem, setSaveProblem] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string>("project");
  const [searchParams, setSearchParams] = useSearchParams();
  // A second click before React re-renders the disabled button must not send a
  // second request: each confirmed save creates a new record.
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // A saved evaluation, opened from History: loaded by the id in the URL, as a
  // query keyed by account and session, and shown as the server stored it.
  // It is not recalculated, and a failure is said as a failure -- never a preset.
  const snapshotParam = searchParams.get("snapshot");
  const snapshotId = snapshotParam && /^[1-9]\d*$/.test(snapshotParam) ? Number(snapshotParam) : null;
  const snapshot = useQuery({
    queryKey: ["history", "detail", user?.user_id ?? "anonymous", epoch, snapshotId],
    queryFn: ({ signal }) => historyApi.getById(snapshotId as number, { signal }),
    enabled: phase === "ready" && snapshotId != null,
    retry: false,
  });
  const closeSnapshot = () => setSearchParams({}, { replace: true });
  const applySnapshotInputs = (it: HistoryItem) => {
    if (it.budget == null || it.co2_reduction == null || it.social_impact == null || it.duration_months == null) return;
    // The stored region is a region, not the country it was computed for: the
    // country is left for the user to choose before anything is calculated.
    setForm({
      project_name: it.name ?? "",
      country: "",
      budget_usd: it.budget,
      co2_reduction_tons_per_year: it.co2_reduction,
      social_impact_score: it.social_impact,
      project_duration_months: it.duration_months,
    });
    setActivePreset("");
  };

  const evalMut = useMutation({ mutationFn: (b: EvaluateRequest) => evaluateApi.evaluate(b) });
  const saveMut = useMutation({ mutationFn: evaluateApi.save });
  const rankMut = useMutation({ mutationFn: evaluateApi.ranking });
  const mcMut = useMutation({ mutationFn: evaluateApi.monteCarlo });
  const explainMut = useMutation({ mutationFn: evaluateApi.explain });
  // A scored result is one that carries a score. `evalMut.data` being `{}` —
  // a 200 with an empty body — used to pass the `!result` guard below, reach
  // `fmtNum(result.budget_usd)` with `undefined`, and take the whole page
  // down with "Cannot read properties of undefined (reading
  // 'toLocaleString')": a blank screen, measured (#236).
  //
  // The formatter is deliberately left strict rather than taught to print a
  // dash for `undefined`. A crash is loud; a dash where a score belongs is
  // the quiet kind of wrong this codebase keeps finding (#216). The fix is
  // to stop calling it with nothing, not to make nothing look printable.
  const rawResult: EvaluateResponse|undefined = run?.result;
  const result: EvaluateResponse|undefined =
    rawResult && rawResult.total_score != null ? rawResult : undefined;
  // Same rule as `result` above, and the same measured crash: `{}` is
  // truthy, so `{explain && <ShapWaterfall/>}` used to hand the panel an
  // object with no `explanation`, and `[...data.explanation]` threw
  // "not iterable" — blanking the page a second way after the first was
  // fixed. An explanation without contributions is not an explanation.
  const rawExplain: ExplainResponse|undefined = explainMut.data;
  const explain: ExplainResponse|undefined =
    rawExplain && Array.isArray(rawExplain.explanation) ? rawExplain : undefined;
  const score = useCountUp(result?.total_score ?? 0);
  const prob  = useCountUp(result?.success_probability ?? 0);
  // The hero's Models and Status are the service's own answer, not constants.
  // While the call is pending, or after the latest attempt fails, there is no
  // answer, so both read "unknown" rather than a healthy default (#236). The
  // query is shared with the header pill (useServiceHealth.ts).
  const service = useServiceSnapshot();
  const countryList = useMemo(()=> countries ? Object.keys(countries).sort() : [], [countries]);
  const setField = <K extends keyof EvaluateRequest>(k:K, v:EvaluateRequest[K]) => setForm(s=>({...s,[k]:v}));
  const applyPreset = (id:string) => { const p=PRESETS.find(x=>x.id===id); if(!p) return; setForm(p.body); setActivePreset(id); };
  const submit = async (mode: "calculate" | "save") => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(mode);
    setSaveProblem(null);
    const payload = { ...form, region: form.country };
    try {
      let save: SaveState = { kind: "none" };
      let answer: EvaluateResponse;
      if (mode === "save") {
        // Saving computes again on the server: what is shown is this call's
        // answer, the one that was stored -- not an earlier calculation.
        const saved = await saveMut.mutateAsync(payload);
        answer = saved;
        save = { kind: "saved", id: saved.evaluation_id };
      } else {
        answer = await evalMut.mutateAsync(payload);
      }
      if (!mounted.current) return;
      setRun({ inputs: payload, result: answer, save });
      if (save.kind === "saved") {
        void qc.invalidateQueries({ queryKey: ["history"] });
        toast.success("Saved as evaluation #" + save.id);
      } else {
        toast.success("Evaluation complete");
      }
      // The explanation and the ranking are extras: their failure is theirs,
      // and does not withdraw a result or a save that already succeeded.
      explainMut.reset();
      rankMut.reset();
      explainMut.mutate(payload, { onError: (e) => { if (!isSessionEnded(e)) toast.error("SHAP explanation unavailable: " + errorMessage(e)); } });
      rankMut.mutate(payload, { onError: (e) => { if (!isSessionEnded(e)) toast.error("Country ranking unavailable: " + errorMessage(e)); } });
    } catch (e: unknown) {
      if (isSessionEnded(e) || !mounted.current) return;
      if (mode === "save") {
        // Nothing on screen may look saved: the previous result was for other
        // inputs, and an id is shown only from a confirmed answer.
        setRun(null);
        setSaveProblem(saveFailureText(e));
      } else {
        toast.error(errorMessage(e));
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(null);
    }
  };

  const downloadPDF = () => {
    // The inputs this result was computed from, not whatever the form holds now.
    const inputs = run?.inputs;
    if (!inputs) return;
    const escapeHtml = (s: string | number | undefined | null): string => {
      const str = String(s ?? "");
      return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#x27;");
    };
    const w = window.open("", "_blank", "width=900,height=1200"); if(!w) return;
    w.opener = null;
    const rows = (result?.recommendations||[]).map(r=>`<li>${escapeHtml(r)}</li>`).join("");
    w.document.write(`<html><head><title>${escapeHtml(inputs.project_name)} · ESG Report</title>
      <style>body{font:14px/1.5 -apple-system,sans-serif;padding:40px;color:#111}
      h1{font-size:28px;margin:0 0 6px}.muted{color:#666;font-size:12px;letter-spacing:.18em;text-transform:uppercase}
      .score{font:600 64px/1 ui-monospace,monospace;color:#0a8a5e;margin:24px 0 4px}
      table{width:100%;border-collapse:collapse;margin:18px 0}td{padding:10px 0;border-bottom:1px solid #eee}
      .k{color:#666;width:40%}.badge{display:inline-block;padding:4px 10px;border-radius:999px;background:#e8f7ef;color:#0a8a5e;font-size:11px;letter-spacing:.18em;text-transform:uppercase}
      </style></head><body>
      <div class="muted">SORA.Earth · ESG Evaluation Report</div>
      <h1>${escapeHtml(inputs.project_name)}</h1>
      <span class="badge">${escapeHtml(result?.risk_level)} risk</span>
      <div class="score">${escapeHtml(result?.total_score?.toFixed(1))}</div>
      <div class="muted">/ 100 · ESG Score · ${escapeHtml(result?.success_probability?.toFixed(1))}% success probability (unvalidated model estimate)</div>
      <table>
        <tr><td class="k">Country</td><td>${escapeHtml(inputs.country)}</td></tr>
        <tr><td class="k">Budget (USD)</td><td>${escapeHtml(inputs.budget_usd.toLocaleString())}</td></tr>
        <tr><td class="k">CO₂ Reduction</td><td>${escapeHtml(inputs.co2_reduction_tons_per_year)} t/yr</td></tr>
        <tr><td class="k">Social Impact</td><td>${escapeHtml(inputs.social_impact_score)} / 10</td></tr>
        <tr><td class="k">Duration</td><td>${escapeHtml(inputs.project_duration_months)} months</td></tr>
        <tr><td class="k">Environment</td><td>${escapeHtml(result?.environment_score?.toFixed(1))}</td></tr>
        <tr><td class="k">Social</td><td>${escapeHtml(result?.social_score?.toFixed(1))}</td></tr>
        <tr><td class="k">Economic</td><td>${escapeHtml(result?.economic_score?.toFixed(1))}</td></tr>
      </table>
      <h3>Recommendations</h3><ul>${rows}</ul>
      <div class="muted" style="margin-top:40px;font-size:10px">Generated ${escapeHtml(new Date().toISOString())} · Formula score + experimental ML estimate · models reported by the service at export: ${escapeHtml(service.models)}</div>
      </body></html>`);
    w.document.close(); setTimeout(()=>w.print(), 300);
  };

  return (
    <div className="ev">
      <section className="ev-hero">
        <div>
          <div className="eyebrow" style={{display:"inline-flex",alignItems:"center",gap:10,color:"var(--planet)",marginBottom:18}}>
            <span style={{width:6,height:6,borderRadius:"50%",background:"var(--planet)",boxShadow:"0 0 0 4px rgba(47,224,166,.18)"}}/>
            Formula score · Experimental ML estimate
          </div>
          <h1 className="display" style={{fontSize:"clamp(36px,5vw,66px)",lineHeight:.98,margin:"0 0 20px"}}>
            Evaluate an <em style={{fontStyle:"italic",color:"var(--planet)"}}>ESG</em> project<br/>from the figures you enter.
          </h1>
          <p style={{color:"var(--muted)",maxWidth:"54ch"}}>A fixed formula scores environment, social and economic factors against country benchmarks. An experimental ML estimate with SHAP explanations sits beside it; it was learned from an administrative proxy, not verified outcomes.</p>
        </div>
        <div className="ev-hero-meta">
          <Row k="Unit"   v={<><span style={{color:"var(--planet)"}}>◆</span> Formula score + experimental ML estimate</>}/>
          <Row k="Models" v={service.models}/>
          <Row k="Status" v={<HealthPill status={service.status}/>}/>
        </div>
      </section>

      {snapshotParam != null && (
        <SnapshotCard key={`${user?.user_id}:${epoch}:${snapshotId}`}
          query={snapshot}
          invalid={snapshotId == null}
          onClose={closeSnapshot}
          onUseInputs={applySnapshotInputs}
        />
      )}

      <div className="ev-grid">
        <aside className="card">
          <div className="card-head"><h3>Project parameters</h3></div>
          <div className="card-body">
            <div className="ev-presets">
              {PRESETS.map(p => <button key={p.id} className={activePreset===p.id?"active":""} onClick={()=>applyPreset(p.id)}>{p.label}</button>)}
            </div>
            <Field label="Project name">
              <input value={form.project_name} onChange={e=>setField("project_name", e.target.value)}/>
            </Field>
            <Field label="Country">
              <Combobox
                value={form.country}
                onChange={v => setField("country", v)}
                options={countryList}
                placeholder="Select country"
              />
            </Field>
            <Field label="Budget (USD)">
              <input type="number" value={form.budget_usd} onChange={e=>setField("budget_usd", Number(e.target.value))}/>
            </Field>
            <Field label="CO₂ Reduction (t/yr)">
              <input type="number" value={form.co2_reduction_tons_per_year} onChange={e=>setField("co2_reduction_tons_per_year", Number(e.target.value))}/>
            </Field>
            <Field label="Social Impact (1–10)">
              <input type="number" min={1} max={10} value={form.social_impact_score} onChange={e=>setField("social_impact_score", Number(e.target.value))}/>
            </Field>
            <Field label="Duration (months)">
              <input type="number" value={form.project_duration_months} onChange={e=>setField("project_duration_months", Number(e.target.value))}/>
            </Field>
            {!form.country && <p className="ev-save-note">Select a country to calculate.</p>}
            <div className="ev-actions">
              <button className="ev-btn" onClick={() => void submit("calculate")} disabled={busy !== null || !form.country}>
                {busy === "calculate" ? "Calculating…" : "Calculate"}
              </button>
              {canSave === "allowed" && (
                <button className="ev-btn ev-btn-save" onClick={() => void submit("save")} disabled={busy !== null || !form.country}>
                  {busy === "save" ? "Saving…" : "Calculate and save"}
                </button>
              )}
            </div>
            {canSave === "anonymous" && (
              <p className="ev-save-note"><Link to="/login?next=%2Fevaluate">Sign in</Link> to save evaluations. Calculating saves nothing.</p>
            )}
            {canSave === "forbidden" && (
              <p className="ev-save-note">Saving is not available for the {user?.role} role. Calculating saves nothing.</p>
            )}
            {saveProblem && <p className="ev-save-problem" role="alert">{saveProblem}</p>}
            {run?.save.kind === "saved" && (
              <p className="ev-save-ok" role="status">
                Saved as evaluation #{run.save.id}. <Link to="/history">View history</Link>
              </p>
            )}
          </div>
        </aside>

        <section className="card ev-result">
          {busy !== null && !result ? <Skeleton/> : !result ? <Empty/> : (
            <>
              <div className="ev-tabs">
                <button className={"ev-tab" + (activeTab==="project" ? " active" : "")} onClick={()=>setActiveTab("project")}>Project</button>
                <button className={"ev-tab" + (activeTab==="ranking" ? " active" : "")} onClick={()=>setActiveTab("ranking")}>Country Ranking</button>
                <button className={"ev-tab" + (activeTab==="mc" ? " active" : "")} onClick={()=>setActiveTab("mc")}>Monte Carlo</button>
                <button className={"ev-tab" + (activeTab==="whatif" ? " active" : "")} onClick={()=>setActiveTab("whatif")}>What-If</button>
              </div>
              {activeTab === "mc" ? (
                <MonteCarlo
                  data={mcMut.data}
                  loading={mcMut.isPending}
                  failed={mcMut.isError}
                  onRun={(n)=>mcMut.mutate({ ...form, region: form.country, n })}
                />
              ) : activeTab === "whatif" ? (
                <>
                  <WhatIf form={form} lastRun={{...run?.inputs, total_score: result.total_score, country: run?.inputs.country || form.country}}/>
                  <UncertaintyCard payload={form}/>
                </>
              ) : activeTab === "ranking" ? (
                <CountryRanking
                  data={rankMut.data}
                  loading={rankMut.isPending}
                  currentCountry={form.country}
                  onPickCountry={(c)=>setField("country", c)}
                />
              ) : (<>
              <div className="ev-result-head">
                <div className="ev-meta mono">
                  <span>{String(run?.inputs.country ?? "").toUpperCase()}</span><span className="sep">·</span>
                  <span>{run?.inputs.project_duration_months} MO</span><span className="sep">·</span>
                  <span>{fmtMoney(run?.inputs.budget_usd ?? 0)}</span>
                </div>
                <span className={"ev-badge "+riskTone[result.risk_level]}>{result.risk_level} risk</span>
              </div>
              <div className="card-body">
                <h2 className="display" style={{fontSize:24,margin:"0 0 4px"}}>{run?.inputs.project_name}</h2>
                <p style={{color:"var(--muted)",fontSize:13,marginBottom:22}}>Weighted index across environment, social and economic axes · region {result.region}</p>
                <div className="ev-score-row">
                  <div>
                    <div className="ev-score tabular">{fmtNum(score,1)}</div>
                    <div className="ev-score-sub">/ 100 · ESG SCORE</div>
                  </div>
                  <div className="ev-prob" title={SUCCESS_PROBABILITY_NOTE}>
                    <div className="big tabular">{fmtNum(prob,1)}%</div>
                    <div className="lbl">{SUCCESS_PROBABILITY_LABEL}</div>
                    <div className="ev-prob-note muted">{SUCCESS_PROBABILITY_NOTE}</div>
                  </div>
                  <button className="ev-pdf" onClick={downloadPDF} disabled={!result}>
                    <span className="dot"/>Download PDF
                  </button>
                </div>
                <div className="ev-bars">
                  <Bar kind="env" k="Environment" v={result.environment_score}/>
                  <Bar kind="soc" k="Social"      v={result.social_score}/>
                  <Bar kind="eco" k="Economic"    v={result.economic_score}/>
                </div>
                {result.recommendations?.length>0 && (
                  <div className="ev-recs">
                    <div className="eyebrow" style={{marginBottom:10}}>Recommendations</div>
                    <ul>{result.recommendations.map((r,i)=><li key={i}>{r}</li>)}</ul>
                  </div>
                )}
              </div>
            </>)}
            </>
          )}
        </section>

        <AnimatePresence>
          {explain && (
            <motion.section className="card ev-shap"
              initial={{opacity:0,y:16}} animate={{opacity:1,y:0}} exit={{opacity:0}}
              transition={{duration:.4,ease:[.16,1,.3,1]}}>
              <div className="card-head">
                <h3>SHAP · Feature contributions</h3>
                <span className="ev-badge low">Explainable</span>
              </div>
              <div className="card-body"><ShapWaterfall data={explain}/></div>
            </motion.section>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

/** What a failed snapshot load says. Each is a failure, never a preset or an empty success. */
function snapshotFailure(e: unknown): string {
  const status = e instanceof ApiError ? e.status : null;
  if (status === 401) return "Sign in to open this evaluation.";
  if (status === 403) return "Saved evaluations are not available for your role.";
  if (status === 404) return "Evaluation not found or not available.";
  return "Could not load the evaluation: " + errorMessage(e);
}

const shown = (v: number | null | undefined, digits = 1) =>
  typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "—";

/**
 * A saved evaluation as the server stored it. Read-only: opening it does not
 * recalculate anything, and a missing column is shown as missing.
 */
function SnapshotCard({ query, invalid, onClose, onUseInputs }: {
  query: { data?: HistoryItem; error: unknown; isPending: boolean; fetchStatus: string };
  invalid: boolean;
  onClose: () => void;
  onUseInputs: (it: HistoryItem) => void;
}) {
  const [pdfPending, setPdfPending] = useState(false);
  const [pdfError, setPdfError] = useState("");
  const it = query.data;
  const downloadSavedPdf = async () => {
    if (!it || pdfPending) return;
    setPdfPending(true); setPdfError("");
    try {
      const blob = await reportApi.savedPdf(it.id);
      const url = URL.createObjectURL(blob);
      try {
        const link = document.createElement("a");
        link.href = url; link.download = `SORA_Evaluation_${it.id}.pdf`;
        document.body.appendChild(link); link.click(); link.remove();
      } finally { URL.revokeObjectURL(url); }
    } catch (error) { if (!isSessionEnded(error)) setPdfError("Could not download the saved report. Please try again."); }
    finally { setPdfPending(false); }
  };
  const complete = !!it && it.budget != null && it.co2_reduction != null && it.social_impact != null && it.duration_months != null;
  let body: React.ReactNode;
  if (invalid) body = <p role="alert" className="ev-snapshot-error">Evaluation not found or not available.</p>;
  else if (query.error) body = <p role="alert" className="ev-snapshot-error">{snapshotFailure(query.error)}</p>;
  else if (!it) body = <p className="ev-snapshot-loading">Loading the saved evaluation…</p>;
  else body = (
    <>
      <h2 className="display" style={{fontSize:22,margin:"0 0 8px"}}>{it.name ?? "Unnamed evaluation"}</h2>
      <div className="ev-snapshot-grid">
        <Row k="ESG score" v={shown(it.total_score)}/>
        <Row k="Environment" v={shown(it.environment_score)}/>
        <Row k="Social" v={shown(it.social_score)}/>
        <Row k="Economic" v={shown(it.economic_score)}/>
        <Row k={SUCCESS_PROBABILITY_LABEL} v={it.success_probability != null ? shown(it.success_probability) + "%" : "—"}/>
        <Row k="Risk" v={it.risk_level ?? "—"}/>
        <Row k="Region" v={it.region ?? "—"}/>
        <Row k="Budget (USD)" v={it.budget != null ? fmtMoney(it.budget) : "—"}/>
        <Row k="CO₂ reduction (t/yr)" v={shown(it.co2_reduction, 0)}/>
        <Row k="Social impact" v={shown(it.social_impact, 1)}/>
        <Row k="Duration (months)" v={it.duration_months ?? "—"}/>
        <Row k="Saved" v={it.created_at ?? "—"}/>
      </div>
      <p className="ev-save-note">
        As stored when it was saved; not recalculated. The record keeps the region, not the country,
        so a new calculation needs a country chosen.
      </p>
      <button type="button" className="ev-btn" disabled={pdfPending} onClick={downloadSavedPdf}>
        {pdfPending ? "Preparing report…" : "Download saved PDF"}
      </button>
      {pdfError && <p role="alert">{pdfError}</p>}
      <button type="button" className="ev-btn" disabled={!complete} onClick={() => onUseInputs(it)}>
        {complete ? "Use these inputs in the form" : "Inputs incomplete: cannot reuse"}
      </button>
    </>
  );
  return (
    <section className="card ev-snapshot" aria-label="Saved evaluation" style={{marginBottom:22}}>
      <div className="card-head">
        <h3>Saved evaluation</h3>
        <button type="button" className="ev-tab" onClick={onClose}>Close</button>
      </div>
      <div className="card-body">{body}</div>
    </section>
  );
}

function Skeleton(){
  return (<div className="ev-skeleton">
    <div className="sk sm"/><div className="sk lg"/><div className="sk md"/>
    <div className="sk bar"/><div className="sk bar" style={{width:"70%"}}/><div className="sk bar" style={{width:"55%"}}/>
    <div className="sk sm"/><div className="sk md"/>
  </div>);
}
function Row({k,v}:{k:string;v:React.ReactNode}) {
  return <div className="ev-hero-row"><div className="k mono">{k}</div><div className="v">{v}</div></div>;
}
function Field({label,children}:{label:string;children:React.ReactNode}) {
  return <div className="ev-field"><label>{label}</label>{children}</div>;
}
function Bar({kind,k,v}:{kind:"env"|"soc"|"eco";k:string;v:number}) {
  const w = clamp(v);
  return (
    <div className={"ev-bar "+kind}>
      <div className="top"><span className="k">{k}</span><span className="v tabular">{fmtNum(v,1)}</span></div>
      <div className="track"><motion.span className="fill" initial={{width:0}} animate={{width:`${w}%`}} transition={{duration:.9,ease:[.16,1,.3,1]}}/></div>
    </div>
  );
}
function Empty() {
  return (
    <div className="ev-empty">
      <div className="icon"/>
      <h4 className="display">No evaluation yet</h4>
      <p>Pick a preset or fill the parameters, then run evaluation to see the ESG score, risk level and SHAP explanation.</p>
    </div>
  );
}
function ShapWaterfall({data}:{data:ExplainResponse}) {
  const feats = [...data.explanation].sort((a,b)=>Math.abs(b.shap_value)-Math.abs(a.shap_value)).slice(0,8);
  const max = Math.max(...feats.map(f=>Math.abs(f.shap_value))) || 1;
  return (
    <div className="shap">
      <div className="shap-head mono">
        <span>f(x) = {data.probability.toFixed(3)}</span>
        <span>E[f(X)] = {data.base_value.toFixed(3)}</span>
      </div>
      <div className="shap-rows">
        {feats.map(f => {
          const w = Math.abs(f.shap_value)/max*100; const pos = f.direction==="positive";
          return (
            <div className="shap-row" key={f.feature}>
              <div className="shap-label"><span className="mono" style={{color:"var(--faint)",marginRight:4}}>{f.value.toFixed(3)}</span> = <b>{f.feature}</b></div>
              <div className="shap-track">
                <motion.span className={"shap-fill "+(pos?"pos":"neg")} initial={{width:0}} animate={{width:`${w}%`}} transition={{duration:.7,ease:[.16,1,.3,1]}}/>
                <span className={"shap-val "+(pos?"pos":"neg")}>{pos?"+":""}{f.shap_value.toFixed(3)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
