import {fetchJSON,ensureArray,kpiRow,table,head} from "/static/js/lib/render.js";
export async function mount(){
  document.getElementById("crumb").textContent="Diagnostics";
  const el=document.getElementById("c-diagnostics");
  el.innerHTML=head("Diagnostics","Platform-wide health metrics");
  try{
    const d=await fetchJSON("/api/v1/admin/diagnostics");
    el.innerHTML+=kpiRow([
      {label:"Period",value:(d.period_hours||24)+"h"},
      {label:"Retrains",value:d.retrain?.total??0},
      {label:"Retrain success",value:d.retrain?.success??0,tone:"good"},
      {label:"Retrain failed",value:d.retrain?.failed??0,tone:d.retrain?.failed?"bad":"good"}
    ]);
    el.innerHTML+=kpiRow([
      {label:"Data refresh total",value:d.data_refresh?.total??0},
      {label:"Data refresh OK",value:d.data_refresh?.success??0,tone:"good"},
      {label:"Predictions",value:d.predictions?.total??0},
      {label:"Avg latency",value:(d.predictions?.avg_latency_ms||0).toFixed(0)+"ms"}
    ]);
    const m=d.retrain?.last_metrics||{};
    el.innerHTML+=`<div class="card" style="padding:16px;margin-top:16px"><div style="font-size:13px;color:var(--c-500);margin-bottom:10px">LAST RETRAIN METRICS</div><div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">${['accuracy','f1_score','roc_auc','best_threshold'].map(k=>`<div><div style="font-size:11px;color:var(--c-500)">${k}</div><div style="font-size:18px;font-weight:600">${m[k]??'—'}</div></div>`).join('')}</div></div>`;
    if(d.top_retrain_errors?.length){
      el.innerHTML+='<div style="margin-top:16px">'+table(d.top_retrain_errors,[{key:"error",label:"Error"},{key:"count",label:"Count"}])+'</div>';
    }
  }catch(e){el.innerHTML+='<div class="card" style="padding:16px;color:#ef4444">Error: '+e.message+'</div>';}
}
