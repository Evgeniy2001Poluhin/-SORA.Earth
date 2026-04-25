import Combobox from "../../components/Combobox";
import CountryRanking from "./CountryRanking";
import MonteCarlo from "./MonteCarlo";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import { evaluateApi } from "@/api/endpoints/evaluate";
import type { EvaluateRequest, EvaluateResponse, ExplainResponse, RiskLevel } from "@/api/types";
import { fmtNum, fmtMoney, clamp } from "@/lib/format";
import "./evaluate.css";

const PRESETS: { id:string; label:string; body:EvaluateRequest }[] = [
  { id:"solar",  label:"Solar Energy",     body:{ project_name:"Solar Farm",           country:"Sweden",  budget_usd:150000, co2_reduction_tons_per_year:120, social_impact_score:9, project_duration_months:18 } },
  { id:"wind",   label:"Wind Farm",        body:{ project_name:"Offshore Wind",        country:"Germany", budget_usd:280000, co2_reduction_tons_per_year:310, social_impact_score:8, project_duration_months:24 } },
  { id:"refo",   label:"Reforestation",    body:{ project_name:"Boreal Reforestation", country:"Canada",  budget_usd:80000,  co2_reduction_tons_per_year:200, social_impact_score:7, project_duration_months:36 } },
  { id:"water",  label:"Water Treatment",  body:{ project_name:"Urban Water Grid",     country:"Spain",   budget_usd:120000, co2_reduction_tons_per_year:60,  social_impact_score:9, project_duration_months:20 } },
  { id:"ev",     label:"EV Infrastructure",body:{ project_name:"Fast-Charge Network",  country:"France",  budget_usd:450000, co2_reduction_tons_per_year:420, social_impact_score:7, project_duration_months:28 } },
  { id:"waste",  label:"Waste Recycling",  body:{ project_name:"Circular Materials",   country:"Italy",   budget_usd:95000,  co2_reduction_tons_per_year:140, social_impact_score:8, project_duration_months:16 } },
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

export function EvaluatePage() {
  const { data: countries } = useQuery({ queryKey:["countries"], queryFn: evaluateApi.countries });
  const [form, setForm] = useState<EvaluateRequest>(PRESETS[0].body);
  const [activePreset, setActivePreset] = useState("solar");
  const [lastRun, setLastRun] = useState<any>(null);
  const [activeTab, setActiveTab] = useState<string>("project");
  const evalMut = useMutation({ mutationFn: evaluateApi.evaluate });
  const rankMut = useMutation({ mutationFn: evaluateApi.ranking });
  const mcMut = useMutation({ mutationFn: evaluateApi.monteCarlo });
  const explainMut = useMutation({ mutationFn: evaluateApi.explain });
  const result: EvaluateResponse|undefined = evalMut.data;
  const explain: ExplainResponse|undefined = explainMut.data;
  const score = useCountUp(result?.total_score ?? 0);
  const prob  = useCountUp(result?.success_probability ?? 0);
  const countryList = useMemo(()=> countries ? Object.keys(countries).sort() : [], [countries]);
  const setField = <K extends keyof EvaluateRequest>(k:K, v:EvaluateRequest[K]) => setForm(s=>({...s,[k]:v}));
  const applyPreset = (id:string) => { const p=PRESETS.find(x=>x.id===id); if(!p) return; setForm(p.body); setActivePreset(id); };
  const run = async () => {
    try {
      const payload = { ...form, region: form.country };
      await Promise.all([ evalMut.mutateAsync(payload), explainMut.mutateAsync(payload), rankMut.mutateAsync(payload) ]);
      setLastRun(payload);
      toast.success("Evaluation complete"); }
    catch(e:any){ toast.error(e.message); }
  };
  useEffect(()=>{
    if (!countryList.length) return;
    if (!countryList.includes(form.country)) {
      // если в списке нет текущей — оставим как есть, но форсанём re-render селекта
      setForm(s=>({...s}));
    }
  }, [countryList]);
  return (
    <div className="ev">
      <section className="ev-hero">
        <div>
          <div className="eyebrow" style={{display:"inline-flex",alignItems:"center",gap:10,color:"var(--planet)",marginBottom:18}}>
            <span style={{width:6,height:6,borderRadius:"50%",background:"var(--planet)",boxShadow:"0 0 0 4px rgba(47,224,166,.18)"}}/>
            ML Scoring · Calibrated ESG Models
          </div>
          <h1 className="display" style={{fontSize:"clamp(36px,5vw,66px)",lineHeight:.98,margin:"0 0 20px"}}>
            Evaluate an <em style={{fontStyle:"italic",color:"var(--planet)"}}>ESG</em> project with<br/>planetary precision.
          </h1>
          <p style={{color:"var(--muted)",maxWidth:"54ch"}}>Score, compare and simulate environment, social and economic impact with explainable ML — powered by SHAP, country-level benchmarks and a closed-loop retraining pipeline.</p>
        </div>
        <div className="ev-hero-meta">
          <Row k="Unit"   v={<><span style={{color:"var(--planet)"}}>◆</span> ML Scoring Engine</>}/>
          <Row k="Models" v="RF · XGB · MLP · Stacking"/>
          <Row k="AUC"    v={<span className="tabular">0.82 prod · 0.98 cv</span>}/>
          <Row k="Status" v={<span className="status-pill">Operational</span>}/>
        </div>
      </section>

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
            <button className="ev-btn" onClick={run} disabled={evalMut.isPending}>
              {evalMut.isPending ? "Evaluating…" : "Run evaluation"}
            </button>
          </div>
        </aside>

        <section className="card ev-result">
          {!result ? <Empty/> : (
            <>
              <div className="ev-tabs">
                <button className={"ev-tab" + (activeTab==="project" ? " active" : "")} onClick={()=>setActiveTab("project")}>Project</button>
                <button className={"ev-tab" + (activeTab==="ranking" ? " active" : "")} onClick={()=>setActiveTab("ranking")}>Country Ranking</button>
                <button className={"ev-tab" + (activeTab==="mc" ? " active" : "")} onClick={()=>setActiveTab("mc")}>Monte Carlo</button>
              </div>
              {activeTab === "mc" ? (
                <MonteCarlo
                  data={mcMut.data as any}
                  loading={mcMut.isPending}
                  onRun={(n)=>mcMut.mutate({ ...form, region: form.country, n })}
                />
              ) : activeTab === "ranking" ? (
                <CountryRanking
                  data={rankMut.data as any}
                  loading={rankMut.isPending}
                  currentCountry={form.country}
                  onPickCountry={(c)=>setField("country", c)}
                />
              ) : (<>
              <div className="ev-result-head">
                <div className="ev-meta mono">
                  <span>{String(lastRun?.region || lastRun?.country || form.country).toUpperCase()}</span><span className="sep">·</span>
                  <span>{lastRun?.project_duration_months ?? form.project_duration_months} MO</span><span className="sep">·</span>
                  <span>{fmtMoney(lastRun?.budget_usd ?? form.budget_usd)}</span>
                </div>
                <span className={"ev-badge "+riskTone[result.risk_level]}>{result.risk_level} risk</span>
              </div>
              <div className="card-body">
                <h2 className="display" style={{fontSize:24,margin:"0 0 4px"}}>{form.project_name}</h2>
                <p style={{color:"var(--muted)",fontSize:13,marginBottom:22}}>Weighted index across environment, social and economic axes · region {result.region}</p>
                <div className="ev-score-row">
                  <div>
                    <div className="ev-score tabular">{fmtNum(score,1)}</div>
                    <div className="ev-score-sub">/ 100 · ESG SCORE</div>
                  </div>
                  <div className="ev-prob">
                    <div className="big tabular">{fmtNum(prob,1)}%</div>
                    <div className="lbl">ML success probability</div>
                  </div>
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
