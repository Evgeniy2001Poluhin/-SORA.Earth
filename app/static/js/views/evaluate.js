import {api} from "/static/js/api.js";
const PRESETS={
  "Solar Energy":{n:"Solar Farm",b:150000,c:120,s:9,d:18},
  "Wind Farm":{n:"Wind Farm",b:120000,c:95,s:8,d:12},
  "Reforestation":{n:"Reforestation",b:80000,c:200,s:7,d:36},
  "Water Treatment":{n:"Water Plant",b:200000,c:40,s:9,d:24},
  "EV Infrastructure":{n:"EV Network",b:180000,c:150,s:8,d:18},
  "Waste Recycling":{n:"Recycling Hub",b:90000,c:60,s:7,d:15}
};
const COUNTRIES=["Germany","France","Spain","Italy","Netherlands","Belgium","Poland","Sweden","Norway","Finland","Denmark","Austria","Portugal","Greece","Czech Republic","Ireland","United Kingdom","United States","Canada","Japan","South Korea","Australia","New Zealand","Brazil","India","China","Mexico","Turkey","Switzerland","Romania"];
export function mount(){
  document.getElementById("crumb").textContent="Evaluate";
  const f=document.getElementById("eval-form");
  const sel=f.querySelector("[name=country]");
  if(sel){sel.innerHTML=COUNTRIES.map(c=>`<option value="${c}">${c}</option>`).join("");}
  document.querySelectorAll(".preset").forEach(b=>b.onclick=e=>{
    e.preventDefault();
    const p=PRESETS[b.textContent.trim()]||{n:b.textContent,b:+b.dataset.b,c:+b.dataset.c,s:7,d:12};
    const setv=(n,v)=>{const el=f.querySelector(`[name=${n}]`);if(el)el.value=v;};
    setv("name",p.n);setv("budget",p.b);setv("co2",p.c);setv("social",p.s);setv("duration",p.d);
  });
  f.onsubmit=async e=>{
    e.preventDefault();const fd=new FormData(e.target);
    const payload={project_name:fd.get("name"),budget_usd:+fd.get("budget"),
      co2_reduction_tons_year:+fd.get("co2"),social_impact:+fd.get("social"),
      project_duration_months:+fd.get("duration"),country:fd.get("country")};
    const r=document.getElementById("result");
    r.innerHTML="<div class=card-head><div class=card-title>Evaluating...</div></div>";
    try{
      const d=await api("/api/v1/evaluate",{method:"POST",body:JSON.stringify(payload)});
      const score=d.total_score||d.esg_score||0;
      const env=d.environment_score||d.environmental||0;
      const soc=d.social_score||d.social||0;
      const eco=d.economic_score||d.economic||0;
      const _p=(d.ml_success_probability??d.success_probability??d.probability??0);
      const succ=_p>1?Math.round(_p):Math.round(_p*100);
      r.innerHTML=`<div class=card-head><div class=card-title>${payload.project_name}</div><span class="badge badge-success badge-dot">${d.risk_level||"Low risk"}</span></div>
        <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:24px">
          <div style="font:500 64px var(--f-sans);letter-spacing:-0.03em;line-height:1">${score.toFixed(1)}</div>
          <div style="font:400 13px var(--f-mono);color:var(--c-600)">/ 100</div></div>
        <div style="display:flex;flex-direction:column;gap:16px">
          ${row("Environment",env)}${row("Social",soc)}${row("Economic",eco)}
          <div style="border-top:1px solid var(--b-subtle);padding-top:16px">${row("ML success probability",succ,"up")}</div>
        </div>`;

      try{
        const resp=await fetch("/api/v1/predict/explain/waterfall",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer super-secret-demo-token"},body:JSON.stringify(payload)});
        if(resp.ok){
          const blob=await resp.blob();
          const url=URL.createObjectURL(blob);
          r.innerHTML+='<div class="card" style="margin-top:16px"><div class="card-head"><div class="card-title">Why '+score.toFixed(1)+'? SHAP waterfall</div><div class="card-subtitle">Feature contributions</div></div><img src="'+url+'" style="width:100%;max-width:720px;display:block;margin:0 auto;background:white;border-radius:8px;padding:12px"></div>';
        }
      }catch(_){}
    }catch(err){r.innerHTML=`<div class=card-head><div class=card-title>Error</div></div><div style="color:var(--s-down);font-size:13px">${err.message}</div>`}
  };
}
function row(l,v,type){const cls=type==="up"?"bar-fill-up":"";
  return `<div><div style="display:flex;justify-content:space-between;margin-bottom:8px;font-size:13px"><span style="color:var(--c-700)">${l}</span><span style="font:500 14px var(--f-mono)">${v.toFixed(1)}${type==="up"?"%":""}</span></div><div class=bar><div class="bar-fill ${cls}" style="width:${Math.min(v,100)}%"></div></div></div>`;
}
