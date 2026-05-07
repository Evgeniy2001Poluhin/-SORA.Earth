#!/usr/bin/env python3
from pathlib import Path
ROOT = Path("app/static")

shap_js = r"""
import {api} from '/static/js/api.js';
export function mount(){
  const crumb=document.getElementById('crumb');if(crumb)crumb.textContent='Predict & Explain';
  const btn=document.getElementById('run-predict');if(btn)btn.onclick=run;
  async function run(){
    const out=document.getElementById('predict-out');
    if(!out)return;
    out.innerHTML='<div class="card"><div class="card-subtitle">Running prediction...</div></div>';
    const payload={country:'Germany',year:2023,co2_emissions:7.5,
      renewable_energy:35,gdp_per_capita:45000,population_density:230,
      forest_area:32,industrial_share:27,energy_intensity:3.5,
      political_stability:0.8,rule_of_law:1.5};
    try{
      const d=await api('/api/v1/predict/explain',{method:'POST',body:JSON.stringify(payload)});
      window.__SHAP_RAW__=d;console.log('[SHAP]',d);
      const pred=d.prediction;
      const prob=d.probability;
      const base=d.base_value||0;
      const verdict=d.verdict||'success';
      const feats=(d.explanation||d.all_features||[]).map(f=>({
        name:f.feature||f.name||'unknown',
        value:+(f.shap_value||0),
        raw:+(f.value||0),
        direction:f.direction||(f.shap_value>=0?'positive':'negative'),
        impact:f.impact||'low'
      })).filter(f=>f.value!==0).sort((a,b)=>Math.abs(b.value)-Math.abs(a.value));
      const max=Math.max(...feats.map(f=>Math.abs(f.value)),0.0001);
      const impactBadge={high:'badge-success',medium:'badge-warn',low:''};

      out.innerHTML='<div class="card">'
        +'<div class="card-head">'
        +'<div><div class="card-title">Prediction class '+pred+'</div>'
        +'<div class="card-subtitle" style="margin-top:4px">Probability '+(prob?prob.toFixed(2)+'%':'—')+' - base value '+(+base).toFixed(4)+'</div></div>'
        +'<span class="bge '+(verdict==='success'?'badge-success':'badge-danger')+' badge-dot">'+verdict+'</span>'
        +'</div>'
        +'<div style="font:500 11px var(--f-mono);color:var(--c-600);text-transform:uppercase;letter-spacing:0.04em;margin:var(--sp-6) 0 var(--sp-4)">Feature attribution (SHAP)</div>'
        +'<div style="display:flex;flex-direction:column;gap:var(--sp-3)">'
        +feats.map(f=>{
          const w=Math.abs(f.value)/max*100;
          const pos=f.direction==='positive';
          const color=pos?'var(--s-up)':'var(--s-down)';
          return '<div style="display:grid;grid-template-columns:200px 1fr 100px 100px 110px;gap:var(--sp-4);align-items:center">'
            +'<div style="color:var(--c-800);font:400 13px var(--f-mono)">'+f.name+'</div>'
            +'<div class="bar" style="position:relative"><div class="bar-fill" style="width:'+w+'%;background:'+color+'"></div></div>'
            +'<div style="font:500 13px var(--f-mono);text-align:right;color:'+color+'">'+(f.value>=0?'+':'')+f.value.toFixed(4)+'</div>'
            +'<span class="badge '+(impactBadge[f.impact]||'')+'" style="justify-content:center">'+f.impact+'</span>'
            +'<span style="text-align:right;font:400 11px var(--f-mono);color:var(--c-600)">'+(pos?'increases':'decreases')+'</span>'
          +'</div>';
        }).join('')
        +'</div></div>';
    }catch(e){out.innerHTML='<div class="card" style="color:var(--s-down)">'+e.message+'</div>'}
  }
}
"""

country_js = r"""
import {api} from '/static/js/api.js';
export function mount(){
  const crumb=document.getElementById('crumb');if(crumb)crumb.textContent='Country benchmark';
  const btn=document.getElementById('run-benchmark');if(btn)btn.onclick=run;
  run();
  async function run(){
    const sel=document.getElementById('country-select');
    const iso=sel?sel.value:'DEU';
    const out=document.getElementById('benchmark-out');
    out.innerHTML='<div class="card"><div class="card-subtitle">Loading...</div></div>';
    try{
      const d=await api('/api/v1/analytics/country-benchmark/'+iso);
      window.__BENCH_RAW__=d;console.log('[Benchmark]',d);
      const b=d.benchmarks||{};
      const meta={
        co2_per_capita:{label:'CO2 per capita',unit:'t/yr',lowerIsBetter:true,ref:4.0,max:15},
        renewable_share:{label:'Renewable share',unit:'%',lowerIsBetter:false,ref:30,max:100},
        esg_rank:{label:'ESG global rank',unit:'#',lowerIsBetter:true,ref:50,max:180},
        hdi:{label:'Human development index',unit:'',lowerIsBetter:false,ref:0.7,max:1},
        gdp_per_capita:{label:'GDP per capita',unit:'USD',lowerIsBetter:false,ref:12000,max:100000},
        gini_index:{label:'Gini inequality',unit:'',lowerIsBetter:true,ref:35,max:60},
        gov_effectiveness:{label:'Government effectiveness',unit:'',lowerIsBetter:false,ref:0,max:2.5},
      };
      out.innerHTML='<div class="card">'
        +'<div class="card-head"><div><div class="card-title">'+(d.country||iso)+'</div>'
        +'<div class="card-subtitle" style="margin-top:4px">ISO '+(d.requested||iso)+' - live World Bank / OECD indicators</div></div></div>'
        +'<table class="table"><thead><tr><th>Indicator</th><th>Benchmark</th><th style="text-align:right">Value</th><th style="text-align:right">Unit</th></tr></thead><tbody>'
        +Object.entries(b).map(([k,v])=>{
          const m=meta[k]||{label:k.replace(/_/g,' '),unit:'',max:100,ref:50,lowerIsBetter:false};
          const pct=Math.min(100,Math.max(0,(v/m.max)*100));
          const good=m.lowerIsBetter?v<=m.ref:v>=m.ref;
          const color=good?'var(--s-up)':'var(--s-warn)';
          return '<tr><td style="color:var(--c-800)">'+m.label+'</td>'
            +'<td style="width:280px"><div class="bar"><div class="bar-fill" style="width:'+pct+'%;background:'+color+'"></div></div></td>'
            +'<td style="text-align:right;font:500 14px var(--f-mono);color:'+color+'">'+v+'</td>'
            +'<td style="text-align:right;color:var(--c-600);font:400 11px var(--f-mono)">'+m.unit+'</td></tr>';
        }).join('')
        +'</tbody></table></div>';
    }catch(e){out.innerHTML='<div class="card" style="color:var(--s-down)">'+e.message+'</div>'}
  }
}
"""

(ROOT / "js/views/shap.js").write_text(shap_js.lstrip())
(ROOT / "js/views/predict.js").write_text(shap_js.lstrip())
(ROOT / "js/views/country.js").write_text(country_js.lstrip())

print("OK: shap.js + predict.js + country.js rewritten with exact API shape")
print("Now hard-reload browser (Cmd+Shift+R) on /app/predict and /app/country")
