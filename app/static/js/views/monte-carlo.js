const K="super-secret-demo-token";
export async function mount(){
  document.getElementById("crumb").textContent="Monte Carlo";
  const el=document.getElementById("c-monte-carlo");
  el.innerHTML='<div class="card"><div class="card-head"><div class="card-title">Parameters</div></div><div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:8px"><input id="mc-b" type="number" value="50000"><input id="mc-c" type="number" value="85"><input id="mc-s" type="number" value="8"><input id="mc-d" type="number" value="12"><input id="mc-n" type="number" value="1000"><select id="mc-t"><option>Solar Energy</option><option>Wind Farm</option></select><select id="mc-co"><option>Germany</option><option>Sweden</option></select><button id="mc-run" class="btn-primary">Run</button></div></div><div id="mc-out" style="margin-top:16px"></div>';
  document.getElementById("mc-run").onclick=async()=>{
    const body={project_type:document.getElementById("mc-t").value,budget_usd:+document.getElementById("mc-b").value,co2_reduction:+document.getElementById("mc-c").value,social_impact:+document.getElementById("mc-s").value,duration_months:+document.getElementById("mc-d").value,country:document.getElementById("mc-co").value,n_simulations:+document.getElementById("mc-n").value};
    const out=document.getElementById("mc-out");
    const rd=d.risk_distribution||{};const ss=d.score_stats||{};const ps=d.probability_stats||{};const risk=d.risk_summary||"-";const tone=risk==="LOW"?"#22c55e":risk==="HIGH"?"#ef4444":"#f59e0b";
out.innerHTML='<div class="card" style="padding:16px;margin-bottom:12px"><div style="font-size:11px;color:var(--c-500);text-transform:uppercase;letter-spacing:.08em">Result · '+d.simulations+' simulations</div></div>'
+'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px">'
+'<div class="card" style="padding:14px"><div style="font-size:11px;color:var(--c-500);text-transform:uppercase">Mean score</div><div style="font-size:22px;font-weight:600;margin-top:4px">'+(ss.mean||0).toFixed(2)+'</div></div>'
+'<div class="card" style="padding:14px"><div style="font-size:11px;color:var(--c-500);text-transform:uppercase">p5 / p95</div><div style="font-size:22px;font-weight:600;margin-top:4px">'+(ss.p5||0).toFixed(1)+' / '+(ss.p95||0).toFixed(1)+'</div></div>'
+'<div class="card" style="padding:14px"><div style="font-size:11px;color:var(--c-500);text-transform:uppercase">Mean probability</div><div style="font-size:22px;font-weight:600;margin-top:4px">'+(ps.mean||0).toFixed(1)+'%</div></div>'
+'<div class="card" style="padding:14px"><div style="font-size:11px;color:var(--c-500);text-transform:uppercase">Risk</div><div style="font-size:22px;font-weight:600;margin-top:4px;color:'+tone+'">'+risk+'</div></div>'
+'</div>'
+'<div class="card" style="padding:16px"><div style="font-size:11px;color:var(--c-500);text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px">Risk distribution</div>'
+'<div style="display:flex;height:28px;border-radius:6px;overflow:hidden;background:var(--c-100)">'
+'<div style="width:'+(rd.low_risk_pct||0)+'%;background:#22c55e"></div>'
+'<div style="width:'+(rd.medium_risk_pct||0)+'%;background:#f59e0b"></div>'
+'<div style="width:'+(rd.high_risk_pct||0)+'%;background:#ef4444"></div>'
+'</div>'
+'<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--c-500);margin-top:8px">'
+'<span>LOW '+(rd.low_risk_pct||0)+'%</span>'
+'<span>MED '+(rd.medium_risk_pct||0)+'%</span>'
+'<span>HIGH '+(rd.high_risk_pct||0)+'%</span>'
+'</div></div>';
    }catch(e){out.innerHTML='<div class="card">Error: '+e.message+'</div>'}
  };
}
