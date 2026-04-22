import {api} from '/static/js/api.js';
export function mount(){
  document.getElementById('crumb').textContent='AI Teammate';
  document.getElementById('run-obs').onclick=()=>run('observe');
  document.getElementById('run-auto').onclick=()=>run('auto');
  async function run(mode){
    const body=document.getElementById('feed-body');
    const stat=document.getElementById('feed-stat');
    stat.textContent='Running '+mode+'...';body.innerHTML='';
    try{
      const r=await api('/api/v1/admin/ai-teammate/run?mode='+mode,{method:'POST'});
      console.log('AI Teammate response:',r);
      const actualMode=r.mode||r.cycle_mode||mode;
      const summary=r.summary||r.message||r.status||('Cycle completed in '+mode+' mode');
      const observations=r.observations||r.observed||r.checks||[];
      const decisions=r.decisions||r.actions||r.recommendations||[];
      stat.textContent='mode='+actualMode+' - '+decisions.length+' decisions, '+observations.length+' observations';
      let html='<div style="padding:var(--sp-4);background:var(--c-100);border-radius:var(--r-md);margin-bottom:var(--sp-4);font-size:13px;color:var(--c-800)">'+summary+'</div>';
      if(observations.length){
        html+='<div style="font:500 11px var(--f-mono);color:var(--c-600);text-transform:uppercase;letter-spacing:0.04em;margin:var(--sp-4) 0 var(--sp-2)">Observations</div>';
        html+=observations.map(o=>{
          const txt=typeof o==='string'?o:(o.message||o.check||o.name||JSON.stringify(o));
          const status=o.status||o.level||'ok';
          return '<div style="display:flex;justify-content:space-between;padding:var(--sp-3);border-bottom:1px solid var(--b-subtle);font-size:13px"><span>'+txt+'</span><span class="badge '+(status==='ok'||status==='healthy'?'badge-success':status==='warning'?'badge-warn':'badge-danger')+'">'+status+'</span></div>';
        }).join('');
      }
      if(decisions.length){
        html+='<div style="font:500 11px var(--f-mono);color:var(--c-600);text-transform:uppercase;letter-spacing:0.04em;margin:var(--sp-6) 0 var(--sp-2)">Decisions</div>';
        html+=decisions.map(d=>{
          const txt=typeof d==='string'?d:(d.message||d.decision||d.reason||JSON.stringify(d));
          const action=d.action||d.type||'info';
          const cat=(d.category||d.area||'').toUpperCase();
          return '<div style="display:flex;gap:var(--sp-4);padding:var(--sp-3);border-bottom:1px solid var(--b-subtle);font-size:13px">'
            +(cat?'<div style="flex-shrink:0;font:400 11px var(--f-mono);color:var(--c-600);width:100px">'+cat+'</div>':'')
            +'<div style="flex:1">'+txt+'</div>'
            +'<span class="badge '+(action==='execute'?'badge-warn':action==='escalate'?'badge-danger':'')+'">'+action+'</span></div>';
        }).join('');
      }
      if(!observations.length && !decisions.length){
        html+='<details style="padding:var(--sp-4);background:var(--c-100);border-radius:var(--r-md);margin-top:var(--sp-4)"><summary style="cursor:pointer;font:500 12px var(--f-mono);color:var(--c-600)">Raw response</summary><pre style="margin-top:var(--sp-3);font:400 11px var(--f-mono);color:var(--c-700);white-space:pre-wrap">'+JSON.stringify(r,null,2)+'</pre></details>';
      }
      body.innerHTML=html;
    }catch(e){body.innerHTML='<div style="color:var(--s-down);font:400 13px var(--f-mono);padding:var(--sp-4)">'+e.message+'</div>'}
  }
}
