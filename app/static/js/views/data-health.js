import {fetchJSON,ensureArray,kpiRow,table,badge,head} from "/static/js/lib/render.js";
export async function mount(){
  document.getElementById("crumb").textContent="Data health";
  const el=document.getElementById("c-data-health");
  el.innerHTML=head("Data health","Null rates and range violations in recent inputs");
  try{
    const d=await fetchJSON("/api/v1/analytics/data-health");
    const nulls=d.null_rates||{};
    const oor=d.out_of_range_rates||{};
    const totalNulls=Object.values(nulls).reduce((s,v)=>s+v,0);
    const totalOor=Object.values(oor).reduce((s,v)=>s+v,0);
    el.innerHTML+=kpiRow([
      {label:"Window",value:(d.window_hours||24)+"h"},
      {label:"Records",value:d.total||0},
      {label:"Null issues",value:totalNulls,tone:totalNulls?"bad":"good"},
      {label:"Out of range",value:totalOor,tone:totalOor?"bad":"good"}
    ]);
    el.innerHTML+='<div style="margin-top:16px">'+
      table(Object.entries(nulls).map(([k,v])=>({field:k,rate:v})),[
        {key:"field",label:"Field"},
        {key:"rate",label:"Null rate",fmt:v=>badge((v*100).toFixed(1)+"%",v?"bad":"good")}
      ])+'</div>';
    el.innerHTML+='<div style="margin-top:16px">'+
      table(Object.entries(oor).map(([k,v])=>({field:k,rate:v})),[
        {key:"field",label:"Check"},
        {key:"rate",label:"Violation rate",fmt:v=>badge((v*100).toFixed(1)+"%",v?"bad":"good")}
      ])+'</div>';
  }catch(e){el.innerHTML+='<div class="card" style="padding:16px;color:#ef4444">Error: '+e.message+'</div>';}
}
