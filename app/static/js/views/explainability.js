const K="super-secret-demo-token";
export function mount(){
  document.getElementById("crumb").textContent="Explainability";
  const el=document.getElementById("c-explainability");
  el.innerHTML="<div class=\"page-head\"><div class=\"page-title\">Global Explainability</div><div class=\"page-subtitle\">SHAP beeswarm across evaluated projects</div></div><div class=\"card\"><div id=\"bw\" style=\"min-height:400px;display:flex;align-items:center;justify-content:center\">Loading...</div></div>";
  fetch("/api/v1/explain/beeswarm",{headers:{"Authorization":"Bearer "+K}})
    .then(r=>r.blob()).then(b=>{
      document.getElementById("bw").innerHTML="<img src=\""+URL.createObjectURL(b)+"\" style=\"width:100%;max-width:1100px;background:white;border-radius:8px;padding:12px\">";
    });
}
