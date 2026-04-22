import {fetchJSON,ensureArray,kpiRow,table,badge,fmtTime,head} from "/static/js/lib/render.js";
export async function mount(){
  document.getElementById("crumb").textContent="Activity timeline";
  const el=document.getElementById("c-activity-timeline");
  el.innerHTML=head("Activity timeline","Retrains, data refreshes, auto-actions");
  try{
    const d=await fetchJSON("/api/v1/admin/timeline");
    const events=ensureArray(d);
    const types=new Set(events.map(e=>e.type));
    el.innerHTML+=kpiRow([
      {label:"Events",value:events.length},
      {label:"Event types",value:types.size},
      {label:"Successful",value:events.filter(e=>e.status==="success").length,tone:"good"},
      {label:"Last",value:events[0]?fmtTime(events[0].timestamp):"-"}
    ]);
    el.innerHTML+=table(events,[
      {key:"timestamp",label:"Time",fmt:fmtTime},
      {key:"type",label:"Type",fmt:v=>badge(v,v==="retrain"?"good":v==="data_refresh"?"warn":"")},
      {key:"status",label:"Status",fmt:v=>badge(v,v==="success"?"good":"bad")},
      {key:"trigger_source",label:"Trigger"},
      {key:"duration_sec",label:"Duration",fmt:v=>v?v.toFixed(2)+"s":"-"},
      {key:"model_version",label:"Version"},
      {key:"message",label:"Message"}
    ]);
  }catch(e){el.innerHTML+='<div class="card" style="padding:16px;color:#ef4444">Error: '+e.message+'</div>';}
}