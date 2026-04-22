const K="super-secret-demo-token";
export async function mount(){
  document.getElementById("crumb").textContent="System summary";
  const el=document.getElementById("c-system-summary");
  const card=(l,v,t)=>'<div class="card"><div class="kpi-label">'+l+'</div><div class="kpi-value '+(t||"")+'">'+(v==null?"—":v)+'</div></div>';
  try{
    const r=await fetch("/api/v1/analytics/summary",{headers:{"X-API-Key":K}});
    const d=await r.json();
    const tm=d.training_metrics||{},ml=d.models_loaded||{},tr=d.traffic||{},pf=d.performance||{};
    const mlRows=Object.entries(ml).map(([k,v])=>'<div style="display:flex;justify-content:space-between;padding:10px 12px;bordertom:1px solid var(--b-subtle)"><span style="font-weight:500">'+k.replace(/_/g," ")+'</span><span class="pill '+(v?"up":"down")+'">'+(v?"loaded":"missing")+'</span></div>').join("");
    const ins=(d.insights||[]).map(i=>'<li style="padding:8px 0;color:var(--c-700);line-height:1.5">'+i+'</li>').join("");
    el.innerHTML=
      '<div class="card" style="padding:24px;background:linear-gradient(135deg,rgba(52,199,89,.08),transparent)"><div style="display:flex;align-items:center;gap:24px"><div style="flex:1"><div class="kpi-label">Readiness</div><div class="kpi-value up" style="font-size:32px;margin-top:4px">'+d.readiness+'</div><div style="margin-top:12px;height:8px;background:var(--b-subtle);border-radius:4px;overflow:hidden"><div style="width:'+d.readiness_score+'%;height:100%;background:var(--c-ok);transition:width .6s"></div></div><div style="font:400 12px var(--f-mono);color:var(--c-500);margin-top:6px">Score '+d.readiness_score+'/100 · window '+d.window_hours+'h</div></div></div></div>'+
      '<div stye="display:grid;grid-template-columns:repeat(6,1fr);gap:16px;margin-top:16px">'+
        card("ROC AUC",tm.roc_auc&&tm.roc_auc.toFixed(3),"up")+
        card("F1 score",tm.f1_score&&tm.f1_score.toFixed(3))+
        card("Accuracy",tm.accuracy&&tm.accuracy.toFixed(3))+
        card("Avg latency",pf.avg_latency_ms?pf.avg_latency_ms.toFixed(0)+"ms":"—")+
        card("Train samples",tm.train_samples)+
        card("Test samples",tm.test_samples)+
      '</div>'+
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px">'+
        '<div class="card"><div class="card-head"><div class="card-title">Models loaded</div></div>'+mlRows+'</div>'+
        '<div class="card"><div class="card-head"><div class="card-title">Insights</div></div><ul style="list-style:disc;padding-left:20px;margin:0">'+ins+'</ul></div>'+
      '</div>'+
      '<div class="card" style="margin-top:16px"><div class="card-head"><div class="card-title">Traffic · '+tr.total_events+' events</div></div><div style="padd:8px 12px;font:400 12px var(--f-mono);color:var(--c-600)">'+JSON.stringify(tr.endpoint_counts||{},null,2)+'</div></div>';
  }catch(e){el.innerHTML='<div class="card">Error: '+e.message+'</div>'}
}
