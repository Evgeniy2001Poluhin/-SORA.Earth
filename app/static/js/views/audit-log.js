import {fetchJSON,ensureArray,kpiRow,table,badge,fmtTime,head} from "/static/js/lib/render.js";
export async function mount(){
  document.getElementById("crumb").textContent="Audit log";
  const el=document.getElementById("c-audit-log");
  el.innerHTML=head("Audit log","Authentication and admin actions");
  try{
    const raw=await fetchJSON("/api/v1/audit/log");
    const arr=ensureArray(raw);
    const fails=arr.filter(r=>r.action==="login_failed"||r.status_code>=400).length;
    el.innerHTML+=kpiRow([
      {label:"Events",value:arr.length},
      {label:"Unique users",value:new Set(arr.map(r=>r.user)).size},
      {label:"Failed",value:fails,tone:fails?"warn":"good"},
      {label:"Last event",value:arr[0]?fmtTime(arr[0].timestamp):"-"}
    ]);
    el.innerHTML+=table(arr,[
      {key:"timestamp",label:"Time",fmt:fmtTime},
      {key:"user",label:"User"},
      {key:"action",label:"Action",fmt:v=>badge(v,v==="login"?"good":v==="login_failed"?"bad":"")},
      {key:"method",label:"Method"},
      {key:"endpoint",label:"Endpoint"},
      {key:"ip",label:"IP"},
      {key:"status_code",label:"Status",fmt:v=>badge(v,v<400?"good":"bad")}
    ]);
  }catch(e){el.innerHTML+='<div class="card" style="padding:16px;color:#ef4444">Error: '+e.message+'</div>';}
}