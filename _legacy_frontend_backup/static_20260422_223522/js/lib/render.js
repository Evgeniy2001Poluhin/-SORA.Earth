export const TOKEN_KEY="sora_jwt";
export const K="super-secret-demo-token";
export function ensureArray(x){if(Array.isArray(x))return x;if(x&&typeof x==="object"){for(const k of ["items","data","predictions","events","batches","results","records","entries","logs","history"]){if(Array.isArray(x[k]))return x[k];}}return [];}
function getTok(){return localStorage.getItem(TOKEN_KEY)||K;}
export async function fetchJSON(u){
  const r=await fetch(u,{headers:{"Authorization":"Bearer "+getTok()},credentials:"include"});
  if(!r.ok) throw new Error("HTTP "+r.status);
  return r.json();
}
export function kpi(l,v,t){const c=t==="good"?"#22c55e":t==="bad"?"#ef4444":t==="warn"?"#f59e0b":"var(--c-900)";return `<div class="card" style="padding:14px 16px"><div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--c-500)">${l}</div><div style="font-size:22px;font-weight:600;margin-top:4px;color:${c}">${v}</div></div>`;}
export function kpiRow(items){return `<div style="display:grid;grid-template-columns:repeat(${Math.min(items.length,4)},1fr);gap:12px;margin-bottom:16px">${items.map(i=>kpi(i.label,i.value,i.tone)).join("")}</div>`;}
export function badge(t,tone){const bg=tone==="good"?"#22c55e22":tone==="bad"?"#ef444422":tone==="warn"?"#f59e0b22":"#64748b22";const fg=tone==="good"?"#22c55e":tone==="bad"?"#ef4444":tone==="warn"?"#f59e0b":"#94a3b8";return `<span style="padding:3px 8px;border-radius:999px;font-size:11px;background:${bg};color:${fg};font-weight:500">${t}</span>`;}
export function table(rows,cols){if(!rows||!rows.length)return '<div style="padding:24px;color:var(--c-500)">No data</div>';const th=cols.map(c=>`<th style="text-align:left;padding:10px 12px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--c-500);border-bottom:1px solid var(--c-200)">${c.label}</th>`).join("");const tb=rows.map(r=>"<tr>"+cols.map(c=>{const v=c.get?c.get(r):r[c.key];const cell=c.fmt?c.fmt(v,r):(v==null?"—":v);return `<td style="padding:10px 12px;border-bottom:1px solid var(--c-100);font-size:13px">${cell}</td>`;}).join("")+"</tr>").join("");return `<div class="card" style="padding:0;overflow:auto"><table style="width:100%;border-collapse:collapse"><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table></div>`;}
export function fmtTime(t){try{return new Date(t).toLocaleString();}catch(e){return t;}}
export function head(t,s){return `<div class="page-head"><div class="page-title">${t}</div>${s?`<div class="page-subtitle">${s}</div>`:""}</div>`;}
