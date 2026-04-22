import {api} from "/static/js/api.js";
export async function mount(){
  document.getElementById("crumb").textContent="Retrain log";
  const el=document.getElementById("c-retrain-log");
  try{
    const d=await api("/api/v1/admin/retrain-log");
    const items=d.items||[];
    const rows=items.map(function(r){
      let m={}; try{ m=JSON.parse(r.metrics_json||"{}"); }catch(_){}
      const pc=r.status==="success"?"up":"down";
      const dur=r.duration_sec?r.duration_sec.toFixed(1)+"s":"—";
      return "<tr><td>#"+r.id+"</td>"+
        "<td>"+new Date(r.started_at).toLocaleString()+"</td>"+
        "<td><span class='pill "+pc+"'>"+r.status+"</span></td>"+
        "<td>"+(r.trigger_source||"—")+"</td>"+
        "<td>"+(m.roc_auc?m.roc_auc.toFixed(4):"—")+"</td>"+
        "<td>"+(m.f1_score?m.f1_score.toFixed(4):"—")+"</td>"+
        "<td>"+(m.accuracy?m.accuracy.toFixed(4):"—")+"</td>"+
        "<td>"+(m.train_samples||"—")+"</td>"+
        "<td>"+dur+"</td>"+
        "<td>"+(r.model_version||"—")+"</td></tr>";
    }).join("");
    const ok=items.filter(function(r){return r.status==="success";}).length;
    const fail=items.length-ok;
    const card=function(l,v,t){return "<div class='card'><div class='kpi-label'>"+l+"</div><div class='kpi-value "+(t||"")+"'>"+v+"</div></div>";};
    el.innerHTML=
      "<div class='kpi-grid'>"+card("Total",items.length)+card("Success",ok,"up")+card("Failed",fail,fail>0?"down":"")+card("Latest",items[0]?items[0].status:"—")+"</div>"+
      "<div class='card'><div class='card-head'><div class='card-title'>Retrain history</div></div>"+
      "<table class='data-table'><thead><tr><th>ID</th><th>Started</th><th>Status</th><th>Trigger</th><th>AUC</th><th>F1</th><th>Accuracy</th><th>Train N</th><th>Duration</th><th>Version</th></tr></thead>"+
      "<tbody>"+rows+"</tbody></table></div>";
  }catch(e){el.innerHTML="<div class='card'>Error: "+e.message+"</div>";}
}
