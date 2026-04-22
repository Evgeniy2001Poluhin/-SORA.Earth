import {fetchJSON,ensureArray,kpiRow,table,fmtTime,head} from "/static/js/lib/render.js";
export async function mount(){
  document.getElementById("crumb").textContent="Predictions log";
  const el=document.getElementById("c-predictions-log");
  el.innerHTML=head("Predictions log","Last inference calls with latency and score");
  try{
    const d=await fetchJSON("/api/v1/analytics/predictions-log");
    const rows=ensureArray(d);
    el.innerHTML+=kpiRow([
      {label:"Total logged",value:rows.length},
      {label:"Avg latency",value:(rows.reduce((s,r)=>s+(r.latency_ms||0),0)/Math.max(rows.length,1)).toFixed(0)+"ms"},
      {label:"Categories",value:new Set(rows.map(r=>r.category)).size},
      {label:"Last",value:rows[0]?fmtTime(rows[0].timestamp):"—"}
    ]);
    el.innerHTML+=table(rows,[
      {key:"id",label:"#"},
      {key:"timestamp",label:"Time",fmt:fmtTime},
      {key:"endpoint",lel:"Endpoint"},
      {key:"category",label:"Category"},
      {key:"region",label:"Region"},
      {key:"esg_total_score",label:"Score",fmt:v=>v?`<b>${v}</b>`:"—"},
      {key:"probability",label:"Prob",fmt:v=>v?v.toFixed(1)+"%":"—"},
      {key:"latency_ms",label:"Latency",fmt:v=>v?v.toFixed(0)+"ms":"—"}
    ]);
  }catch(e){el.innerHTML+='<div class="card" style="padding:16px;color:#ef4444">Error: '+e.message+'</div>';}
}
