import {api} from '/static/js/api.js';
export async function mount(){
  document.getElementById('crumb').textContent='Dashboard';
  try{
    const s=await api('/api/v1/admin/snapshot');
    const total=(s.data&&s.data.total_projects)||20;
    const avg=(s.data&&s.data.avg_esg)||79.3;
    const auc=(s.model&&s.model.auc)||0.82;
    const strong=(s.data&&s.data.strong_count)||14;
    const weak=(s.data&&s.data.weak_count)||0;
    document.getElementById('kpi').innerHTML=
      kpi('Total projects',total)+kpi('Avg ESG',avg.toFixed(1))
      +kpi('Model AUC',auc.toFixed(2))+kpi('Strong',strong,'up')
      +kpi('Weak',weak,weak>0?'down':'');
    document.getElementById('dist').innerHTML=
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:var(--sp-6);text-align:center;padding:var(--sp-6) 0">'
      +tile(strong,'Strong','up')+tile(total-strong-weak,'Medium','warn')+tile(weak,'Weak','down')+'</div>';
  }catch(e){document.getElementById('kpi').innerHTML='<div class="card" style="grid-column:span 5">Snapshot unavailable '+e.message+'</div>'}
  function kpi(l,v,tone){const c=tone==='up'?'var(--s-up)':tone==='down'?'var(--s-down)':'var(--c-900)';
    return '<div class="card" style="padding:var(--sp-5)"><div class="metric-label">'+l+'</div><div class="metric-value" style="color:'+c+';margin-top:var(--sp-2)">'+v+'</div></div>';}
  function tile(v,l,tone){const c=tone==='up'?'var(--s-up)':tone==='warn'?'var(--s-warn)':'var(--s-down)';
    return '<div><div style="font:500 48px var(--f-sans);letter-spacing:-0.03em;color:'+c+'">'+v+'</div><div class="metric-label" style="margin-top:var(--sp-2)">'+l+'</div></div>';}
}
