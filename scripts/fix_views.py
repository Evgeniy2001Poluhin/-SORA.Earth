#!/usr/bin/env python3
"""Fix undefined bugs in views after first run."""
from pathlib import Path

ROOT = Path("app/static")

# === FIX 1: SHAP explain — probe API response shape first ===
shap_js = r"""
import {api} from '/static/js/api.js';
export function mount(){
  document.getElementById('crumb').textContent='Predict & Explain';
  const btn=document.getElementById('run-predict');
  if(btn)btn.onclick=run;
  async function run(){
    const out=document.getElementById('predict-out');
    out.innerHTML='<div class="card-subtitle">Running...</div>';
    const payload={country:'Germany',year:2023,co2_emissions:7.5,
      renewable_energy:35,gdp_per_capita:45000,population_density:230,
      forest_area:32,industrial_share:27,energy_intensity:3.5,
      political_stability:0.8,rule_of_law:1.5};
    try{
      const d=await api('/api/v1/predict/explain',{method:'POST',body:JSON.stringify(payload)});
      console.log('SHAP response:',d);
      const pred=d.prediction??d.predicted_class??'-';
      const ba=d.base_value??d.expected_value??0;
      // Try multiple shapes
      let feats=d.shap_contributions||d.features||d.shap_values||[];
      if(!Array.isArray(feats) && typeof feats==='object'){
        feats=Object.entries(feats).map(([name,value])=>({name,value}));
      }
      feats=feats.map(f=>({
        name:f.name||f.feature||f.feature_name||'unknown',
        value:+(f.value??f.shap_value??f.contribution??0)
      })).sort((a,b)=>Math.abs(b.value)-Math.abs(a.value)).slice(0,10);
      const max=Math.max(...feats.map(f=>Math.abs(f.value)),0.001);
      out.innerHTML=
        '<div class="card"><div class="card-head"><div><div class="card-title">Prediction '+pred+'</div>'
        +'<div class="card-subtitle">Base value '+(+base).toFixed(4)+'</div></div>'
        +'<span class="badge badge-success badge-dot">success</span></div>'
        +'<div style="display:flex;flex-direction:column;gap:var(--sp-3)">'
        +feats.map(f=>{
          const w=Math.abs(f.value)/max*100;
          const pos=f.value>=0;
          return '<div style="display:grid;grid-template-columns:180px 1fr 80px 140px;gap:var(--sp-4);align-items:center;font-size:13px">'
          +'<div style="color:var(--c-700);font:400 12px var(--f-mono)">'+f.name+'</div>'
          +'<div class="bar"><div class="bar-fill '+(pos?'bar-fill-up':'')+'" style="width:'+w+'%;'+(!pos?'background:var(--s-down)':'')+'"></div></div>'
          +'<div style="font:500 12px var(--f-mono);text-align:right;color:'+(pos?'var(--s-up)':'var(--s-down)')+'">'+(pos?'+':'')+f.value.toFixed(4)+'</div>'
          +'<span class="badge '+(pos?'badge-success':'badge-danger')+'">'+(pos?'↑ increases':'↓ decreases')+'</span></div>';
        }).join('')
        +'</div></div>';
    }catch(e){out.innerHTML='<div class="castyle="color:var(--s-down)">'+e.message+'</div>'}
  }
}
"""

# === FIX 2: AI Teammate — probe and render flexibly ===
ai_js = r"""
import {api} from '/static/js/api.js';
export function mount(){
  document.getElementById('crumb').textContent='AI Teammate';
  document.getElementById('run-obs').onclick=()=>run('observe');
  document.getEmentById('run-auto').onclick=()=>run('auto');
  async function run(mode){
    const body=document.getElementById('feed-body');
    const stat=document.getElementById('feed-stat');
    stat.textContent='Running '+mode+'...';body.innerHTML='';
    try{
      const r=await api('/api/v1/admin/ai-teammate/run?mode='+mode,{method:'POST'});
      console.log('AI Teammate response:',r);
      const actualMode=r.mode||r.cycle_mode||mode;
      const summary=r.summary||r.message||r.status||('Cycle completed in '+mode+' mode');
      const observations=r.observations||r.observed||r.checks||[];
      const decisions=r.decisions||r.actions||r.recommendations||[];
      stat.textContent='mode='+actualMode+' - '+decisions.length+' decisions, '+observations.length+' observations';
      let html='<div style="padding:var(--sp-4);background:var(--c-100);border-radius:var(--r-md);margin-bottom:var(--sp-4);font-size:13px;color:var(--c-800)">'+summary+'</div>';
      if(observations.length){
        html+='<div style="font:500 11px var(--f-mono);color:var(--c-600);text-transform:uppercase;letter-spacing:0.04em;margin:var(--sp-4) 0 var(--sp-2)">Observations</div>';
        html+=observations.map(o=>{
          const txt=typeof o==='string'?o:(o.message||o.check||o.name||JSON.stringify(o));
          const status=o.status||o.level||'ok';
          return '<div style="display:flex;justify-content:space-between;padding:var(--sp-3);border-bottom:1px solid var(--b-subtle);font-size:13px"><span>'+txt+'</span><span class="badge '+(status==='ok'||status==='healthy'?'badge-success':status==='warning'?'badge-warn':'badge-danger')+'">'+status+'</span></div>';
        }).join('');
      }
      if(decisions.length){
        html+='<div style="font:500 11px var(--f-mono);color:var(--c-600);text-transform:uppercase;letter-spacing:0.04em;margin:var(--sp-6) 0 var(--sp-2)">Decisions</div>';
        html+=decisions.map(d=>{
          const txt=typeof d==='string'?d:(d.message||d.decision||d.reason||JSON.stringify(d));
          const action=d.action||d.type||'info';
          const cat=(d.category||d.area||'').toUpperCase();
          return '<div style="display:flex;gap:var(--sp-4);padding:var(--sp-3);border-bottom:1px solid var(--b-subtle);font-size:13px">'
            +(cat?'<div style="flex-shrink:0;font:400 11px var(--f-mono);color:var(--c-600);width:100px">'+cat+'</div>':'')
            +'<div style="flex:1">'+txt+'</div>'
            +'<span class="badge '+(action==='execute'?'badge-warn':action==='escalate'?'badge-danger':'')+'">'+action+'</span></div>';
        }).join('');
      }
      if(!observations.length && !decisions.length){
        html+='<details style="padding:var(--sp-4);background:var(--c-100);border-radius:var(--r-md);margin-top:var(--sp-4)"><summary style="cursor:pointer;font:500 12px var(--f-mono);color:var(--c-600)">Raw response</summary><pre style="margin-top:var(--sp-3);font:400 11px var(--f-mono);color:var(--c-700);white-space:pre-wrap">'+JSON.stringify(r,null,2)+'</pre></details>';
      }
      body.innerHTML=html;
    }catch(e){body.innerHTML='<div style="color:var(--s-down);font:400 13px var(--f-mono);padding:var(--sp-4)">'+e.message+'</div>'}
  }
}
"""

# === FIX 3: Country Benchmark — table instead of raw JSON ===
country_html = r"""<div class="page-head">
<div class="page-title">Country benchmark</div>
<div class="page-subtitle">Per-country ESG indicators sourced from World Bank and OECD. Compare against global averages.</div>
</div>
<div class="card" style="margin-bottom:var(--sp-6)">
<div class="card-head"><div class="card-title">Select country</div></div>
<div style="display:grid;grid-template-columns:1fr auto;gap:var(--sp-4);align-items:end">
<div><label class="label">Country</label>
<select class="select" id="country-select">
<option value="DEU">Germany</option><option value="U">United States</option>
<option value="JPN">Japan</option><option value="FRA">France</option>
<option value="GBR">United Kingdom</option><option value="CHN">China</option>
<option value="IND">India</option><option value="BRA">Brazil</option>
<option value="RUS">Russia</option><option value="ZAF">South Africa</option>
</select></div>
<button class="btn btn-primary" id="run-benchmark">Compare</button>
</div></div>
<div id="benchmark-out"></div>"""

country_js = r"""
import {api} from '/static/js/api.js';
export function mount(){
  document.getElementById('crumb').textContent='Country benchmark';
  document.getElementById('run-benchmark').onclick=run;
  async function run(){
    const iso=document.getElementById('country-select').value;
    const out=document.getElementById('benchmark-out');
    out.innerHTML='<div class="card"><div class="card-subtitle">Loading...</div></div>';
    try{
      const d=await api('/api/v1/analytics/country-benchmark/'+iso);
      console.log('Benchmark:',d);
      const b=d.benchmarks||d.indicators||d.metrics||d;
      const rows=[
        ['CO2 per capita (t)','co2_per_capita'],
        ['Renewable share (%)','renewable_share'],
        ['ESG rank','esg_rank'],
        ['HDI','hdi'],
        ['GDP per capita (USD)','gdp_per_capita'],
        ['Gini index','gini_index'],
        ['Government effectiveness','gov_effectiveness']
      ];
      out.innerHTML=
        '<div class="card"><div class="card-head"><div class="card-title">'+(d.country||iso)+'</div><div class="card-subtitle">ISO '+(d.requested||iso)+'</div></div>'
        +'<table class="table"><thead><tr><th>Indicator</th><th style="text-align:right">Value</th></tr></thead><tbody>'
        +rows.filter(([,k])=>b[k]!=null).map(([label,k])=>
          '<tr><td style="color:var(--c-700)">'+label+'</td><td style="text-align:right;font:500 14px var(--f-mono)">'+b[k]+'</td></tr>'
        ).join('')
        +'</tbody></table></div>';
    }catch(e){out.innerHTML='<div class="card" style="color:var(--s-down)">'+e.message+'</div>'}
  }
}
"""

# === Write fixes ===
fixes = {
    "js/views/shap.js": shap_js,
    "js/views/predict.js": shap_js,  # alias
    "js/views/ai-teammate.js": ai_js,
    "views/country.html": country_html,
    "js/views/country.js": country_js,
}
for path, content in fixes.items():
    (ROOT / path).write_text(content.lstrip())
    print("FIXED:", path)

# === Also patch router to include new routes ===
router = ROOT / "js/router.js"
src = router.read_text()
if "'/app/country'" not in src:
    new_routes = """const routes={
  '/app':'evaluate','/app/evaluate':'evaluate','/app/dashboard':'dashboard',
  '/app/country':'country','/app/shap':'shap','/app/predict':'shap',
  '/admin':'snapshot','/admin/snapshot':'snapshot','/admin/ai-teammate':'ai-teammate',
};"""
    import re
    src = re.sub(r"const routes=\{[^}]*\};", new_routes, src, count=1)
    router.write_text(src)
    print("FIXED: router.js routes expanded")

print("\nDone. Rebuild:  docker compose restart app  (or reload browser if static is volume-mounted)")
