const K="super-secret-demo-token";
export async function mount(){
  document.getElementById("crumb").textContent="Country ranking";
  const el=document.getElementById("c-country-ranking");
  try{
    const r=await fetch("/api/v1/analytics/country-ranking?limit=30",{headers:{"X-API-Key":K}});
    const d=await r.json();
    const items=d.data||[];
    const pillCls=v=>v>=60?"up":v>=30?"":"down";
    const rows=items.map(function(c,i){
      return "<tr><td>#"+(c.esg_rank||i+1)+"</td>"+
        "<td>"+c.country+"</td>"+
        "<td>"+c.hdi.toFixed(3)+"</td>"+
        "<td>$"+c.gdp_per_capita.toLocaleString()+"</td>"+
        "<td>"+c.gini_index.toFixed(1)+"</td>"+
        "<td>"+c.co2_per_capita.toFixed(1)+"</td>"+
        "<td><span class=\"pill "+pillCls(c.renewable_share)+"\">"+c.renewable_share.toFixed(1)+"%</span></td>"+
        "<td>"+c.gov_effectiveness.toFixed(2)+"</td></tr>";
    }).join("");
    const avg=(k)=>items.length?(items.reduce((s,c)=>s+c[k],0)/items.length):0;
    const best=items[0]||{};
    const worst=items[items.length-1]||{};
    const kpi=(l,v)=>"<div class=\"card\"><div class=\"kpi-label\">"+l+"</div><div class=\"kpi-value\">"+v+"</div></div>";
    el.innerHTML=
      "<div style=\"display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:16px\">"+
        kpi("Countries",d.total)+
        kpi("Best",best.country||"-")+
        kpi("Worst",worst.country||"-")+
        kpi("Avg renewable",avg("renewable_share").toFixed(1)+"%")+
      "</div>"+
      "<div class=\"card\"><div class=\"card-head\"><div class=\"card-title\">ESG ranking \u00b7 "+d.total+" countries</div></div>"+
      "<table class=\"data-table\" style=\"width:100%;border-collapse:collapse\"><thead><tr style=\"text-align:left;border-bottom:1px solid var(--b-default)\"><th>Rank</th><th>Country</th><th>HDI</th><th>GDP/cap</th><th>Gini</th><th>CO2/cap</th><th>Renewable</th><th>Gov eff</th></tr></thead>"+
      "<tbody>"+rows+"</tbody></table></div>";
  }catch(e){el.innerHTML="<div class=\"card\">Error: "+e.message+"</div>";}
}
