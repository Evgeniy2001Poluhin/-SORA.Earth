export const h = (html) => { const t=document.createElement("template"); t.innerHTML=html.trim(); return t.content.firstChild; };
export const qs = (s,r=document)=>r.querySelector(s);
export const qsa = (s,r=document)=>Array.from(r.querySelectorAll(s));

export function Card({title,subtitle,body,actions=""}){
  return `<section class="s-card">
    <div style="display:flex;justify-content:space-between;align-items:start;gap:16px;margin-bottom:4px">
      <div>${title?`<h3>${title}</h3>`:""}${subtitle?`<p class="sub">${subtitle}</p>`:""}</div>
      ${actions?`<div>${actions}</div>`:""}
    </div>${body||""}</section>`;
}
export function Field({name,label,type="text",value="",opts}){
  if(type==="select"){
    const o=(opts||[]).map(v=>`<option ${v===value?"selected":""}>${v}</option>`).join("");
    return `<div class="s-field"><label>${label}</label><select name="${name}">${o}</select></div>`;
  }
  return `<div class="s-field"><label>${label}</label><input name="${name}" type="${type}" value="${value}"></div>`;
}
export function Bar(label,v,max=100){
  return `<div class="s-bar"><div class="head"><span>${label}</span><span class="val">${(+v).toFixed(1)}</span></div>
    <div class="s-bar-track"><div class="s-bar-fill" style="width:${Math.min(v/max*100,100)}%"></div></div></div>`;
}
export function KPI(k,v){ return `<div class="s-kpi"><div class="k">${k}</div><div class="v">${v}</div></div>`; }
export function Badge(text,kind="ok"){ return `<span class="s-badge s-badge-${kind}">${text}</span>`; }
export function Empty(title,sub,action=""){ return `<div class="s-empty"><h4>${title}</h4><p>${sub||""}</p>${action}</div>`; }
export function Error(msg){ return `<div class="s-error">${msg}</div>`; }
export function Skeleton(n=3){ return Array(n).fill(`<div class="s-skel"></div>`).join(""); }
export function Table(cols,rows){
  return `<table class="s-table"><thead><tr>${cols.map(c=>`<th>${c}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
export function crumb(text){ const el=document.getElementById("crumb"); if(el) el.textContent=text; }
export function outlet(){ return document.getElementById("outlet-tab")||document.getElementById("outlet"); }
