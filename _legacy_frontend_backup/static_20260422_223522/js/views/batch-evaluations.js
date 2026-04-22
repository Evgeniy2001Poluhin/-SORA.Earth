import {fetchJSON,ensureArray,kpiRow,table,badge,fmtTime,head} from "/static/js/lib/render.js";
export async function mount(){
  document.getElementById("crumb").textContent="Batch evaluations";
  const el=document.getElementById("c-batch-evaluations");
  el.innerHTML=head("Batch evaluations","Bulk scoring runs");
  try{
    const raw=await fetchJSON("/api/v1/batch");
    const arr=ensureArray(raw);
    const okCount=arr.filter(r=>r.status==="completed").length;
    el.innerHTML+=kpiRow([
      {label:"Batches",value:arr.length},
      {label:"Completed",value:okCount,tone:"good"},
      {label:"Partial/Failed",value:arr.length-okCount,tone:arr.length-okCount?"warn":"good"},
      {label:"Total runs",value:arr.reduce((s,r)=>s+(r.total||0),0)}
    ]);
    el.innerHTML+=table(arr,[
      {key:"batch_id",label:"Batch ID",fmt:v=>"<code>"+v+"</code>"},
      {key:"total",label:"Total"},
      {key:"successful",label:"OK"},
      {key:"failed",label:"Failed"},
      {key:"status",label:"Status",fmt:v=>badge(v,v==="completed"?"good":v==="partial"?"warn":"bad")},
      {key:"duration_ms",label:"Duration",fmt:v=>(v/1000).toFixed(2)+"s"},
      {key:"created_at",label:"When",fmt:fmtTime}
    ]);
  }catch(e){el.innerHTML+='<div class="card" style="padding:16px;color:#ef4444">Error: '+e.message+'</div>';}
}