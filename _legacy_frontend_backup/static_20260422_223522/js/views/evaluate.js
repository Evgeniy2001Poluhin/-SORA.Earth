import {api} from "/static/js/api.js";
import {h,qs,qsa,Card,Field,Bar,KPI,Table,Empty,Error as Err,Skeleton,Badge,crumb,outlet} from "/static/js/ui.js";

const PRESETS={
  "Solar Energy":{n:"Solar Farm",b:150000,c:120,s:9,d:18},
  "Wind Farm":{n:"Wind Farm",b:120000,c:95,s:8,d:12},
  "Reforestation":{n:"Reforestation",b:80000,c:200,s:7,d:36},
  "Water Treatment":{n:"Water Plant",b:200000,c:40,s:9,d:24},
  "EV Infrastructure":{n:"EV Network",b:180000,c:150,s:8,d:18},
  "Waste Recycling":{n:"Recycling Hub",b:90000,c:60,s:7,d:15}
};
const COUNTRIES=["Germany","France","Spain","Italy","Netherlands","Belgium","Poland","Sweden","Norway","Finland","Denmark","Austria","Portugal","Greece","Czech Republic","Ireland","United Kingdom","United States","Canada","Japan","South Korea","Australia","New Zealand","Brazil","India","China","Mexico","Turkey","Switzerland","Romania"];

function ensureCSS(){
  if(qs('link[data-sora="1"]')) return;
  const l=document.createElement("link"); l.rel="stylesheet"; l.href="/static/css/sora.css"; l.dataset.sora="1";
  const t=document.createElement("link"); t.rel="stylesheet"; t.href="/static/css/sora-theme.css"; t.dataset.soraTheme="1"; document.head.appendChild(t);
  document.head.appendChild(l);
}

export function mount(){
  ensureCSS(); crumb("Evaluate");
  const host=outlet(); if(!host) return;
  const tab=new URLSearchParams(location.search).get("tab")||"evaluate";
  if(tab==="country-ranking") return renderRanking(host);
  if(tab==="monte-carlo")    return renderMonteCarlo(host);
  if(tab==="snapshot")       return renderSnapshot(host);
  return renderProject(host);
}

/* ---------- Project ---------- */
function renderProject(host){
  const form=`
    <div class="s-presets">
      ${Object.keys(PRESETS).map(k=>`<button type="button" data-p="${k}">${k}</button>`).join("")}
    </div>
    <form id="f">
      ${Field({name:"name",label:"Project name",value:"Solar Farm"})}
      ${Field({name:"country",label:"Country",type:"select",opts:COUNTRIES,value:"Sweden"})}
      ${Field({name:"budget",label:"Budget (USD)",type:"number",value:150000})}
      ${Field({name:"co2",label:"CO₂ reducon (t/yr)",type:"number",value:120})}
      ${Field({name:"social",label:"Social impact (1-10)",type:"number",value:9})}
      ${Field({name:"duration",label:"Duration (months)",type:"number",value:18})}
      <button id="run" type="button" class="s-btn s-btn-primary">Run evaluation</button>
    </form>`;
  host.innerHTML=`
    <div class="s-grid">
      <aside>${Card({title:"Project parameters",subtitle:"Calibrated ML models · ESG + SHAP.",body:form})}</aside>
      <div class="s-stack">
        <div id="result">${Card({title:"Result",subtitle:"Run an evaluation on the left to see ESG score, sub-scores and SHAP explanation."})}</div>
        <div id="shap"></div>
      </div>
    </div>`;

  const f=qs("#f",host);
  qsa(".s-presets button",host).forEach(b=>b.onclick=()=>{const p=PRESETS[b.dataset.p];f.name.value=p.n;f.budget.value=p.b;f.co2.value=p.c;f.social.value=p.s;f.duration.value=p.d;});
  qs("#run",host).onclick=async()=>{
    const payload={
  project_name: (f.name.value||'').trim() || 'Project',
  country: f.country.value,
  budget_usd: Number((f.budget.value||'').toString().replace(/[, _]/g,'')) || 0,
  co2_reduction_tons_year: Number(f.co2.value||0),
  social_impact: Number(f.social.value||0),
  project_duration_months: Number(f.duration.value||0)
};
    const R=qs("#result",host), S=qs("#shap",host); S.innerHTML="";
    R.innerHTML=Card({title:"Evaluating…",subtitle:"Calling /api/v1/evaluate",body:Skeleton(4)});
    try{
      const d=await api("/api/v1/evaluate",{method:"POST",body:JSON.stringify(payload)});
      const score=d.total_score??d.esg_score??0, env=d.environment_score??0, soc=d.social_score??0, eco=d.economic_score??0;
      const p=d.ml_success_probability??d.success_probability??0, succ=p>1?Math.round(p):Math.round(p*100);
      R.innerHTML=Card({
        title:`${String(payload.country||'').toUpperCase()} · ${Number(payload.project_duration_months ?? 0)} MO · $${(Number(payload.budget_usd)||0).toLocaleString('en-US')}`,
        subtitle:"Weighted index across environment, social and economic axes.",
        actions:Badge(d.risk_level||"Low risk","ok"),
        body:`<div style="font:600 14px var(--f-sans);letter-spacing:.01em;color:#F3F7FB;margin-bottom:10px">${payload.project_name}</div>
          <div class="s-score"><div class="num">${score.toFixed(1)}</div><div class="lbl">/ 100 ESG SCORE</div>
            <div class="side" style="text-align:right"><div class="big" style="font-size:30px;line-height:1;font-weight:700;color:#37D6C8">${succ}%</div><div class="lbl" style="font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:rgba(201,210,224,.62)">ML SUCCESS PROBABILITY</div></div></div>
          <div class="s-bars">${Bar("Environment",env)}${Bar("Social",soc)}${Bar("Economic",eco)}</div>`
      });
      try{
        const r=await fetch("/api/v1/predict/explain/waterfall",{method:"POST",
          headers:{"Content-Type":"application/json","Authorization":"Bearer super-secret-demo-token"},
          body:JSON.stringify(payload)});
        if(r.ok){ const url=URL.createObjectURL(await r.blob());
          S.innerHTML=`<div class="s-shap"><img src="${url}" alt="SHAP waterfall"></div>`; }
      }catch(_){}
    }catch(e){ R.innerHTML=Card({title:"Evaluation failed",body:Err(e.message)}); }
  };
}

/* ---------- Country ranking ---------- */
async function renderRanking(host){
  host.innerHTML=Card({title:"Country ranking",subtitle:"ESG benchmark across supported countries.",body:`<div id="t">${Skeleton(6)}</div>`});
  try{
    const d=await api("/api/v1/analytics/country-ranking?limit=30");
    const rows=(d.ranking||d.items||d).map((r,i)=>[i+1,r.country,(r.esg_score??r.score??0).toFixed(2),Badge(r.tier||"—","ok")]);
    qs("#t",host).innerHTML=Table(["#","Country","ESG Score","Tier"],rows);
  }catch(e){ qs("#t",host).innerHTML=Err(e.message); }
}

/* ---------- Monte Carlo ---------- */
function renderMonteCarlo(host){
  host.innerHTML=`<div class="s-grid">
    <div id="mcres">${Card({title:"Monte Carlo",subtitle:"Run a simulation to see distribution of outcomes."})}</div>
    <aside>${Card({title:"Simulation parameters",body:`
      <form id="mf">
        ${Field({name:"country",label:"Country",type:"select",opts:COUNTRIES,value:"Germany"})}
        ${Field({name:"budget",label:"Budget (USD)",type:"number",value:150000})}
        ${Field({name:"iterations",label:"Iterations",type:"number",value:1000})}
        <button id="mrun" type="butt" class="s-btn s-btn-primary">Run simulation</button>
      </form>`})}</aside></div>`;
  qs("#mrun",host).onclick=async()=>{
    const f=qs("#mf",host), R=qs("#mcres",host);
    R.innerHTML=Card({title:"Simulating…",body:Skeleton(4)});
    try{
      const d=await api("/api/v1/analytics/monte-carlo",{method:"POST",body:JSON.stringify({
        country:f.country.value,budget_usd:+f.budget.value,iterations:+f.iterations.value})});
      const m=d.statistics||d;
      R.innerHTML=Card({title:"Distribution results",body:`<div class="s-kpis">
        ${KPI("Mean",(m.mean||0).toFixed(2))}${KPI("P5",(m.p5||m.percentile_5||0).toFixed(2))}
        ${KPI("Median",(m.median||0).toFixed(2))}${KPI("P95",(m.p95||m.percentile_95||0).toFixed(2))}
        ${KPI("Std",(m.std||0).toFixed(2))}${KPI("Iterations",d.iterations||f.iterations.value)}</div>`});
    }catch(e){ R.innerHTML=Card({title:"Simulation failed",body:Err(e.message)}); }
  };
}

/* ---------- Snapshot ---------- */
async function renderSnapshot(host){
  hosinnerHTML=Card({title:"Platform snapshot",subtitle:"Live operational readiness and model state.",body:`<div id="snap">${Skeleton(5)}</div>`});
  try{
    const d=await api("/api/v1/admin/snapshot");
    const k=d.kpis||d, m=d.models||{};
    const kpis=`<div class="s-kpis">
      ${KPI("ROC AUC",(k.roc_auc??k.auc??0).toFixed(3))}
      ${KPI("F1",(k.f1_score??0).toFixed(3))}
      ${KPI("Accuracy",(k.accuracy??0).toFixed(3))}
      ${KPI("Avg latency",`${Math.round(k.avg_latency_ms||k.latency_ms||0)} ms`)}
      ${KPI("Train",k.train_samples??"—")}
      ${KPI("Test",k.test_samples??"—")}</div>`;
    const models=Object.keys(m).length?`<h3 style="margin-top:22px">Models loaded</h3>
      <div class="s-kpis">${Object.entries(m).map(([n,s])=>KPI(n,s.loaded?"loaded":"—")).join("")}</div>`:"";
    qs("#snap",host).innerHTML=kpis+models;
  }catch(e){ qs("#snap",host).innerHTML=Err(e.message); }
}
