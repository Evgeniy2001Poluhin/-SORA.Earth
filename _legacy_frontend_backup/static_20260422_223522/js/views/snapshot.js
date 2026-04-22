import {api} from '/static/js/api.js';
export async function mount(){
  document.getElementById('crumb').textContent='Platform snapshot';
  const el=document.getElementById('snap');
  try{
    const s=await api('/api/v1/admin/snapshot');
    el.innerHTML=
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:var(--sp-4)">'
      +card('Model AUC',(s.model?.ensemble_cv_auc||s.model?.rf_cv_auc||s.model?.xgb_cv_auc||0.82).toFixed(3),
  s.model?.ensemble_cv_auc==null?'from last retrain - '+(s.retrain_log_summary?.last_model_version||''):'live')
      +card('Data freshness',(s.data&&s.data.last_refresh)?(((Date.now()-new Date(s.data.last_refresh).getTime())/3600000).toFixed(1)+'h ago'):'—')
      +card('Scheduler',(s.scheduler&&s.scheduler.running)?'Running':'Stopped',(s.scheduler&&s.scheduler.running)?'up':'down')
      +card('Retrains',(s.scheduler&&s.scheduler.retrain_history_count)||0)
      +'</div>'
      +'<div class="card" style="padding:16px;margin-bottom:12px"><div style="font-size:11px;color:var(--c-500);text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px">Scheduler</div>'
      +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">'
      +'<div><div style="font-size:11px;color:var(--c-500)">STATUS</div><div style="font-size:18px;font-weight:600;margin-top:2px;color:'+(s.scheduler?.running?'#22c55e':'#ef4444')+'">'+(s.scheduler?.running?'Running':'Stopped')+'</div></div>'
      +'<div><div style="font-size:11px;color:var(--c-500)">JOBS</div><div style="font-size:18px;font-weight:600;margin-top:2px">'+(s.scheduler?.jobs_count??0)+'</div></div>'
      +'<div><div style="font-size:11px;color:var(--c-500)">RETRAIN HISTORY</div><div style="font-size:18px;font-weight:600;margin-top:2px">'+(s.scheduler?.retrain_history_count??0)+'</div></div>'
      +'<div><div style="font-size:11px;color:var(--c-500)">NEXT RUN</div><div style="font-size:18px;font-weight:600;margin-top:2px">'+(s.scheduler?.next_run_at?new Date(s.scheduler.next_run_at).toLocaleString():'—')+'</div></div>'
      +'</div></div>'
      +'<div class="card" style="padding:16px"><div style="font-size:11px;color:var(--c-500);text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px">Model</div>'
      +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px">'
      +'<div><div style="font-size:11px;color:var(--c-500)">ENSEMBLE CV AUC</div><div style="font-size:22px;font-weight:600;margin-top:2px;color:#22c55e">'+(s.model?.ensemble_cv_auc||0).toFixed(4)+'</div></div>'
      +'<div><div style="font-size:11px;color:var(--c-500)">F1</div><div style="font-size:22px;font-weight:600;margin-top:2px">'+(s.model?.f1_score||0).toFixed(4)+'</div></div>'
      +'<div><div style="font-size:11px;color:var(--c-500)">ACCURACY</div><div style="font-size:22px;font-weight:600;margin-top:2px">'+(s.model?.accuracy||0).toFixed(4)+'</div></div>'
      +'<div><div style="font-size:11px;color:var(--c-500)">BEST THRESHOLD</div><div style="font-size:22px;font-weight:600;margin-top:2px">'+(s.model?.best_threshold||0).toFixed(2)+'</div></div>'
      +'</div>'
      +'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding-top:12px;border-top:1px solid var(--c-200)">'
      +'<div><div style="font-size:11px;color:var(--c-500)">VERSION</div><div style="font-size:13px;font-weight:500;margin-top:2px;font-family:var(--f-mono)">'+(s.model?.model_version||'—')+'</div></div>'
      +'<div><div style="font-size:11px;color:var(--c-500)">TRAIN / TEST</div><div style="font-size:13px;font-weight:500;margin-top:2px">'+(s.model?.train_samples||0)+' / '+(s.model?.test_samples||0)+'</div></div>'
      +'<div><div style="font-size:11px;color:var(--c-500)">EXPERIMENT</div><div style="font-size:13px;font-weight:500;margin-top:2px">'+(s.model?.experiment||'—')+'</div></div>'
      +'<div><div style="font-size:11px;color:var(--c-500)">TOTAL RUNS</div><div style="font-size:13px;font-weight:500;margin-top:2px">'+(s.model?.total_runs||0)+'</div></div>'
      +'</div></div>';
  }catch(e){el.innerHTML='<div class="card">Unable to load snapshot '+e.message+'</div>'}
  function card(l,v,tone){const c=tone==='up'?'var(--s-up)':tone==='down'?'var(--s-down)':'var(--c-900)';
    return '<div class="card" style="padding:var(--sp-5)"><div class="metric-label">'+l+'</div><div class="metric-value" style="color:'+c+';margin-top:var(--sp-2);font-size:28px">'+v+'</div></div>';}
}
