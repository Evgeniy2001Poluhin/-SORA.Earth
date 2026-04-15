document.addEventListener("DOMContentLoaded", function(){
window.scrollTo(0,0);document.documentElement.scrollTop=0;document.body.scrollTop=0;
var sb=document.querySelector(".sidebar");if(sb)sb.scrollTop=0;
var mn=document.querySelector(".main");if(mn)mn.scrollTop=0;


var stackChart=null,shapChart=null,distChart=null,featChart=null,trendsChart=null,leafletMap=null;

window.getEvalData=function(){
  return {name:document.getElementById("e-name").value,
    budget:+document.getElementById("e-budget").value,
    co2_reduction:+document.getElementById("e-co2").value,
    social_impact:+document.getElementById("e-social").value,
    duration_months:+document.getElementById("e-duration").value,
    region:document.getElementById("e-country").value};
};
var TEMPLATES={solar:{name:"Solar Panel Initiative",budget:50000,co2:85,social:8,duration:12},wind:{name:"Wind Farm Project",budget:120000,co2:95,social:6,duration:24},forest:{name:"Reforestation",budget:25000,co2:60,social:9,duration:36},water:{name:"Water Treatment",budget:40000,co2:30,social:8,duration:18},ev:{name:"EV Charging Network",budget:200000,co2:120,social:7,duration:12},waste:{name:"Waste Recycling Center",budget:15000,co2:45,social:8,duration:6},
agriculture:{name:"Smart Agriculture",budget:75000,co2:55,social:7,duration:18},
transport:{name:"Green Transport Hub",budget:300000,co2:200,social:6,duration:30},
education:{name:"Eco Education Center",budget:20000,co2:15,social:10,duration:12},
ocean:{name:"Ocean Cleanup Initiative",budget:150000,co2:70,social:8,duration:24}};

window.showPage=function(id,el){
  document.querySelectorAll(".page").forEach(function(p){p.classList.remove("active")});
  var target=document.getElementById("page-"+id);
  if(target)target.classList.add("active");
  document.querySelectorAll(".nav-item").forEach(function(n){n.classList.remove("active")});
  if(el)el.classList.add("active");
  if(id==="dashboard")loadDashboard();
  if(id==="map")setTimeout(initMap,100);
  if(id==="trends")loadTrends();
  if(id==="metrics")loadMetrics();
  setTimeout(function(){document.querySelector(".main").scrollTop=0;},50);
};

window.fillTemplate=function(key){
  var t=TEMPLATES[key];
  document.getElementById("e-name").value=t.name;
  document.getElementById("e-budget").value=t.budget;
  document.getElementById("e-co2").value=t.co2;
  document.getElementById("e-social").value=t.social;
  document.getElementById("e-duration").value=t.duration;
};

window.evaluateProject=function(){
  var d={name:document.getElementById("e-name").value,budget:+document.getElementById("e-budget").value,co2_reduction:+document.getElementById("e-co2").value,social_impact:+document.getElementById("e-social").value,duration_months:+document.getElementById("e-duration").value,region:document.getElementById("e-country").value};
  fetch("/api/v1/evaluate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)}).then(function(r){return r.json()}).then(function(j){
    document.getElementById("eval-result").style.display="block";
    document.getElementById("eval-header").textContent=d.name+" — ESG "+j.total_score+", Risk "+j.risk_level+", Success "+j.success_probability+"%";
    document.getElementById("scoreNum").textContent=j.total_score;
    drawScoreRing(j.total_score);
    var rc=j.risk_level==="Low"?"badge-low":j.risk_level==="Medium"?"badge-medium":"badge-high";
    document.getElementById("riskBadge").innerHTML="<span class='risk-badge "+rc+"'>Risk: "+j.risk_level+"</span>";
    document.getElementById("probVal").textContent=j.success_probability+"%";
    var pb=document.getElementById("evalProbBar");if(pb)pb.style.width=j.success_probability+"%";
    var bars=[{label:"Environment",value:j.environment_score,color:"#00e5a0",weight:"40%"},{label:"Social Impact",value:j.social_score,color:"#00c9db",weight:"30%"},{label:"Economic",value:j.economic_score,color:"#00e5a0",weight:"30%"}];
    var bhtml="";
    bars.forEach(function(b){bhtml+="<div class='esg-bar'><div class='dot' style='background:"+b.color+"'></div><div class='bar-label'>"+b.label+"<div class='bar-sub'>Weight: "+b.weight+"</div></div><div class='bar-track'><div class='bar-fill' style='width:"+b.value+"%;background:"+b.color+"'></div></div><div class='bar-value' style='color:"+b.color+"'>"+b.value+"%</div></div>"});
    document.getElementById("esgBars").innerHTML=bhtml;
    if(j.recommendations&&j.recommendations.length>0){document.getElementById("recBox").style.display="block";document.getElementById("recBox").innerHTML=j.recommendations.map(function(r){return"<div style=\"margin-bottom:8px;padding-left:12px;border-left:3px solid #00e5a0\">"+r+"</div>"}).join("")}
    loadEvalHistory();
  }).catch(function(e){console.error("evaluate error",e)});
};

function drawScoreRing(score){
  var c=document.getElementById("scoreCanvas");if(!c)return;
  var ctx=c.getContext("2d");ctx.clearRect(0,0,180,180);
  ctx.beginPath();ctx.arc(90,90,80,0,Math.PI*2);ctx.strokeStyle="#1e2a45";ctx.lineWidth=10;ctx.stroke();
  ctx.beginPath();ctx.arc(90,90,80,-Math.PI/2,-Math.PI/2+(Math.PI*2*score/100));ctx.strokeStyle="#00e5a0";ctx.lineWidth=10;ctx.lineCap="round";ctx.stroke();
}

function loadEvalHistory(){
  fetch("/api/v1/history").then(function(r){return r.json()}).then(function(rows){
    var tb=document.getElementById("evalHistBody");if(!tb)return;
    var html="";
    rows.slice(0,10).forEach(function(r){
      var rc=r.risk_level==="Low"?"badge-low":r.risk_level==="Medium"?"badge-medium":"badge-high";
      html+="<tr><td>"+r.name+"</td><td>"+r.total_score+"</td><td>"+r.success_probability+"%</td><td><span class='badge "+rc+"'>"+r.risk_level+"</span></td></tr>";
    });
    tb.innerHTML=html;
  }).catch(function(e){console.error("history error",e)});
}

window.predictStacking=function(){
  var d={budget:+document.getElementById("s-budget").value,co2_reduction:+document.getElementById("s-co2").value,social_impact:+document.getElementById("s-social").value,duration_months:+document.getElementById("s-duration").value};
  fetch("/predict/stacking",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)}).then(function(r){return r.json()}).then(function(j){
    var box=document.getElementById("stackResult");box.style.display="block";
    var prob=j.probability||0;
    var pred=j.prediction;
    box.className="result-panel "+(pred===1?"result-success":"result-fail");
    document.getElementById("stackTitle").textContent=pred===1?"✅ Project Likely Successful":"❌ Project Likely Unsuccessful";
    document.getElementById("stackProb").textContent=(prob*100).toFixed(1)+"%";
    var mg="";
    var ip=j.individual_probs||{};
    var keys=Object.keys(ip);
    keys.forEach(function(k){mg+="<div class='model-box'><div class='m-name'>"+k+"</div><div class='m-value'>"+(ip[k]*100).toFixed(1)+"%</div></div>"});
    mg+="<div class='model-box'><div class='m-name'>stacking</div><div class='m-value'>"+(prob*100).toFixed(1)+"%</div></div>";
    document.getElementById("stackModels").innerHTML=mg;
    drawStackChart(ip,prob);
    loadShap(d);
  }).catch(function(e){console.error("stacking error",e)});
};

function drawStackChart(probs,sp){
  if(stackChart)stackChart.destroy();
  var ctx=document.getElementById("stackChart");if(!ctx)return;
  var labels=Object.keys(probs);labels.push("stacking");
  var vals=Object.keys(probs).map(function(k){return(probs[k]*100).toFixed(1)});vals.push((sp*100).toFixed(1));
  stackChart=new Chart(ctx,{type:"bar",data:{labels:labels,datasets:[{data:vals,backgroundColor:["#00e5a0","#00c9db","#f59e0b","#8b5cf6"],borderRadius:6}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,max:100,ticks:{color:"#8892a8",callback:function(v){return v+"%"}},grid:{color:"#1e2a45"}},x:{ticks:{color:"#e2e8f0"},grid:{display:false}}}}});
}

function loadShap(d){
  fetch("/explain/shap",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)}).then(function(r){return r.json()}).then(function(j){
    if(!j.features)return;
    if(shapChart)shapChart.destroy();
    var ctx=document.getElementById("shapChart");if(!ctx)return;
    var colors=j.shap_values.map(function(v){return v>=0?"#00e5a0":"#ef4444"});
    shapChart=new Chart(ctx,{type:"bar",data:{labels:j.features,datasets:[{data:j.shap_values,backgroundColor:colors,borderRadius:4}]},options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:"#8892a8"},grid:{color:"#1e2a45"}},y:{ticks:{color:"#e2e8f0"},grid:{display:false}}}}});
  }).catch(function(e){console.error("shap error",e)});
}

window.whatIf=function(){
  var d={name:document.getElementById("e-name").value,budget:+document.getElementById("e-budget").value,co2_reduction:+document.getElementById("e-co2").value,social_impact:+document.getElementById("e-social").value,duration_months:+document.getElementById("e-duration").value,region:document.getElementById("e-country").value};
  fetch("/what-if",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)}).then(function(r){return r.json()}).then(function(j){
    var msg="What-If Analysis:\n\n";var vars=j.variations||{};
    for(var k in vars){msg+=k+": score "+vars[k].score_change.toFixed(2)+" ("+vars[k].new_score+"), prob "+vars[k].prob_change.toFixed(2)+"% ("+vars[k].new_probability+"%)\n"}
    alert(msg);
  });
};

function loadDashboard(){
  fetch("/api/v1/history").then(function(r){return r.json()}).then(function(rows){
    var total=rows.length;
    var avgEsg=total>0?(rows.reduce(function(s,r){return s+r.total_score},0)/total).toFixed(1):0;
    var avgProb=total>0?(rows.reduce(function(s,r){return s+r.success_probability},0)/total).toFixed(1):0;
    var strong=rows.filter(function(r){return r.total_score>=75}).length;
    var weak=rows.filter(function(r){return r.total_score<50}).length;
    var ds=document.getElementById("dashStats");
    if(ds)ds.innerHTML="<div class='stat-card'><div class='label'>Total Projects</div><div class='value'>"+total+"</div></div><div class='stat-card'><div class='label'>Avg ESG</div><div class='value'>"+avgEsg+"</div></div><div class='stat-card'><div class='label'>Avg Success</div><div class='value'>"+avgProb+"%</div></div><div class='stat-card'><div class='label'>Strong</div><div class='value' style='color:#10b981'>"+strong+"</div></div>";
    var seen={};rows.forEach(function(r){if(!seen[r.name]||r.total_score>seen[r.name].total_score)seen[r.name]=r});var sorted=Object.values(seen).sort(function(a,b){return b.total_score-a.total_score});
    var topHtml="";sorted.slice(0,5).forEach(function(r,i){
      var c=i<3?"#00e5a0":"#8892a8";
      topHtml+="<div style='display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #1e2a45'><div><span style='color:"+c+";font-weight:700;margin-right:8px'>"+(i+1)+"</span>"+r.name+"</div><div style='color:#00e5a0;font-weight:700'>"+r.total_score+"</div></div>";
    });
    var tp=document.getElementById("topProjects");if(tp)tp.innerHTML=topHtml;
    if(distChart)distChart.destroy();
    var ctx=document.getElementById("distChart");if(!ctx)return;
    distChart=new Chart(ctx,{type:"bar",data:{labels:["Strong (75+)","Medium (50-75)","Weak (<50)"],datasets:[{data:[strong,total-strong-weak,weak],backgroundColor:["#00e5a0","#f59e0b","#ef4444"],borderRadius:6}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{ticks:{color:"#8892a8"},grid:{color:"#1e2a45"}},x:{ticks:{color:"#e2e8f0"},grid:{display:false}}}}});


    var btnRow=document.getElementById("dashButtons");
    if(!btnRow){
      btnRow=document.createElement("div");btnRow.id="dashButtons";
      btnRow.style.cssText="display:flex;gap:12px;margin:16px 0;justify-content:flex-end";
      btnRow.innerHTML='<button onclick="window.location.href=\'/export/csv\'" style="background:linear-gradient(135deg,#00e5a0,#06b6d4);color:#0a0e1a;border:none;padding:10px 20px;border-radius:8px;font-weight:600;cursor:pointer;font-size:13px">📥 Export CSV</button><button onclick="if(nfirm(\'Clear all history?\'))fetch(\'/history\',{method:\'DELETE\'}).then(function(){loadDashboard()})" style="background:linear-gradient(135deg,#ef4444,#f97316);color:white;border:none;padding:10px 20px;border-radius:8px;font-weight:600;cursor:pointer;font-size:13px">🗑 Clear History</button>';
      var dashPage=document.getElementById("page-dashboard");
      dashPage.insertBefore(btnRow,dashPage.firstChild);
    }

    var fctx=document.getElementById("featChart");
    if(fctx){
      if(featChart)featChart.destroy();
      var feats=["Budget","CO2 Reduction","Social Impact","Duration","Country"];
      var imps=[0.28,0.25,0.22,0.15,0.10].map(function(v){return+(v*100).toFixed(1)});
      featChart=new Chart(fctx,{type:"bar",data:{labels:feats,datasets:[{data:imps,backgroundColor:["#00e5a0","#06b6d4","#8b5cf6","#f59e0b","#ef4444"],borderRadius:6}]},options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:"#8892a8"},grid:{color:"#1e2a45"}},y:{ticks:{color:"#e2e8f0"},grid:{display:false}}}}});
    }
  });
}

function initMap(){
  if(leafletMap)return;
  var el=document.getElementById("map");if(!el)return;
  leafletMap=L.map("map",{attributionControl:false,minZoom:2,maxBounds:[[-85,-180],[85,180]]}).setView([20,10],2);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png",{attribution:""}).addTo(leafletMap);
  fetch("/api/v1/history").then(function(r){return r.json()}).then(function(rows){
    var regions={};
    rows.forEach(function(r){
      var lat=r.lat||50,lon=r.lon||10,reg=r.region||"Europe";
      if(!regions[reg])regions[reg]={count:0};regions[reg].count++;
      var color=r.total_score>=75?"#00e5a0":r.total_score>=50?"#f59e0b":"#ef4444";
      L.circleMarker([lat,lon],{radius:8,fillColor:color,color:color,fillOpacity:0.6,weight:1}).bindPopup("<b>"+r.name+"</b><br>ESG: "+r.total_score+"<br>Success: "+r.success_probability+"%").addTo(leafletMap);
    });
    var emojis={"Europe":"\uD83C\uDDEA\uD83C\uDDFA","North America":"\uD83C\uDDFA\uD83C\uDDF8","South America":"\uD83C\uDDE7\uD83C\uDDF7","Asia":"\uD83C\uDDE8\uD83C\uDDF3","Africa":"\uD83C\uDDFF\uD83C\uDDE6","Oceania":"\uD83C\uDDE6\uD83C\uDDFA"};
    var rcHtml="";for(var reg in regions){rcHtml+="<div class='region-card'><div class='emoji'>"+(emojis[reg]||"\uD83C\uDF0D")+"</div><div><div class='r-name'>"+reg+"</div><div class='r-count'>"+regions[reg].count+" projects</div></div></div>"}
    var rc=document.getElementById("regionCards");if(rc)rc.innerHTML=rcHtml;
  });
}

window.compareProjects=function(){
  var cards=document.querySelectorAll("#compareInputs .card");
  var projects=[];
  cards.forEach(function(card){
    var inps=card.querySelectorAll("input");
    projects.push({name:inps[0].value,budget:+inps[1].value,co2_reduction:+inps[2].value,social_impact:+inps[3].value,duration_months:+inps[4].value,region:inps[5]?inps[5].value:"Germany"});
  });
  Promise.all(projects.map(function(p){
    return fetch("/api/v1/evaluate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)}).then(function(r){return r.json()});
  })).then(function(results){
    var winner=results.reduce(function(best,r,i){return r.total_score>best.score?{score:r.total_score,idx:i}:best},{score:0,idx:0});
    var html="<div class='card' style='text-align:center;background:rgba(0,229,160,0.08);border-color:rgba(0,229,160,0.3)'><div style='color:#8892a8;font-size:12px;text-transform:uppercase'>Winner</div><div style='font-size:28px;font-weight:700;color:#00e5a0;margin:8px 0'>"+projects[winner.idx].name+"</div><div style='color:#8892a8'>ESG: "+results[winner.idx].total_score+" | Success: "+results[winner.idx].success_probability+"%</div></div>";
    html+="<div class='card'><table class='table'><thead><tr><th>Metric</th>";
    projects.forEach(function(p){html+="<th>"+p.name+"</th>"});
    html+="</tr></thead><tbody>";
    [["Total ESG","total_score"],["Environment","environment_score"],["Social","social_score"],["Economic","economic_score"],["Success %","success_probability"],["Risk","risk_level"]].forEach(function(m){
      html+="<tr><td>"+m[0]+"</td>";results.forEach(function(r){html+="<td>"+r[m[1]]+"</td>"});html+="</tr>";
    });
    html+="</tbody></table></div>";
    var cr=document.getElementById("compareResult");if(cr){cr.style.display="block";cr.innerHTML=html}
  });
};

window.calcGHG=function(){
  var d={electricity_kwh:+document.getElementById("g-elec").value,natural_gas_m3:+document.getElementById("g-gas").value,diesel_liters:+document.getElementById("g-diesel").value,petrol_liters:+document.getElementById("g-petrol").value,flights_km:+document.getElementById("g-flights").value,waste_kg:+document.getElementById("g-waste").value};
  fetch("/ghg-calculate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)}).then(function(r){return r.json()}).then(function(j){
    var el=document.getElementById("ghgResult");if(!el)return;el.style.display="block";
    var rc=j.rating==="Excellent"?"#10b981":j.rating==="Good"?"#00e5a0":j.rating==="Average"?"#f59e0b":"#ef4444";
    var tot=Math.max(j.total_tons_co2,0.01);
    var html="<div style='text-align:center;margin:20px 0'><div style='font-size:48px;font-weight:700;color:"+rc+"'>"+j.total_tons_co2+"</div><div style='color:#8892a8'>tons CO2/year</div><div class='risk-badge' style='background:rgba(0,229,160,0.1);color:"+rc+";margin-top:8px'>"+j.rating+"</div></div>";
    html+="<div class='esg-bar'><div class='bar-label'>Scope 1</div><div class='bar-track'><div class='bar-fill fill-green' style='width:"+Math.min(j.scope1/tot*100,100)+"%'></div></div><div class='bar-value'>"+j.scope1+"t</div></div>";
    html+="<div class='esg-bar'><div class='bar-label'>Scope 2</div><div class='bar-track'><div class='bar-fill fill-blue' style='width:"+Math.min(j.scope2/tot*100,100)+"%'></div></div><div class='bar-value'>"+j.scope2+"t</div></div>";
    html+="<div class='esg-bar'><div class='bar-label'>Scope 3</div><div class='bar-track'><div class='bar-fill fill-yellow' style='width:"+Math.min(j.scope3/tot*100,100)+"%'></div></div><div class='bar-value'>"+j.scope3+"t</div></div>";
    html+="<div class='rec-box'>"+j.tip+"</div>";
    document.getElementById("ghgContent").innerHTML=html;
  });
};

function loadTrends(){
  fetch("/trends").then(function(r){return r.json()}).then(function(data){
    if(trendsChart)trendsChart.destroy();if(!data||data.length===0)return;
    var ctx=document.getElementById("trendsChart");if(!ctx)return;
    trendsChart=new Chart(ctx,{type:"line",data:{labels:data.map(function(d){return d.date}),datasets:[{label:"ESG Score",data:data.map(function(d){return d.score}),borderColor:"#00e5a0",backgroundColor:"rgba(0,229,160,0.1)",fill:true,tension:0.4},{label:"Success %",data:data.map(function(d){return d.prob}),borderColor:"#00c9db",backgroundColor:"rgba(0,201,219,0.1)",fill:true,tension:0.4}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:"#e2e8f0"}}},scales:{y:{ticks:{color:"#8892a8"},grid:{color:"#1e2a45"}},x:{ticks:{color:"#8892a8",maxTicksLimit:10},grid:{display:false}}}}});
  });
}

function loadMetrics(){
  fetch("/system/metrics").then(function(r){return r.json()}).then(function(m){
    var mc=document.getElementById("metricsCards");if(!mc)return;
    mc.innerHTML="<div class='stat-card'><div class='label'>Total Requests</div><div class='value'>"+(m.requests_total||0)+"</div></div><div class='stat-card'><div class='label'>Predictions</div><div class='value'>"+(m.predictions_total||0)+"</div></div><div class='stat-card'><div class='label'>Errors</div><div class='value' style='color:#ef4444'>"+(m.errors_total||0)+"</div></div><div class='stat-card'><div class='label'>Avg Response</div><div class='value'>"+(m.avg_response_time_ms||0)+"ms</div></div>";
  }).catch(function(){
    fetch("/metrics").then(function(r){return r.text()}).then(function(t){
      try{var m=JSON.parse(t);var mc=document.getElementById("metricsCards");if(mc)mc.innerHTML="<div class='stat-card'><div class='label'>Total Requests</div><div class='value'>"+(m.requests_total||0)+"</div></div><div class='stat-card'><div class='label'>Predictions</div><div class='value'>"+(m.predictions_total||0)+"</div></div><div class='stat-card'><div class='label'>Errors</div><div class='value' style='color:#ef4444'>"+(m.errors_total||0)+"</div></div><div class='stat-card'><div class='label'>Avg Response</div><div class='value'>"+(m.avg_response_time_ms||0)+"ms</div></div>"}catch(e){}
    });
  });
  fetch("/predictions/history?limit=20").then(function(r){return r.json()}).then(function(j){
    var preds=j.predictions||j||[];
    var html="";preds.reverse().forEach(function(p){
      html+="<tr><td>"+(p.timestamp||"—")+"</td><td>$"+Number(p.budget||0).toLocaleString()+"</td><td>"+(p.co2_reduction||"—")+"</td><td><span class='badge "+(p.prediction==1?"badge-low":"badge-high")+"'>"+(p.prediction==1?"Success":"Fail")+"</span></td><td>"+(p.probability?(p.probability*100).toFixed(1)+"%":"—")+"</td></tr>";
    });
    var pb=document.getElementById("predHistBody");if(pb)pb.innerHTML=html;
  }).catch(function(e){console.error("pred history error",e)});
}

function initCompare(){
  var defaults=[{name:"Solar Panel Initiative",budget:50000,co2:85,social:8,dur:12},{name:"Wind Farm Project",budget:120000,co2:95,social:6,dur:24},{name:"Community Garden",budget:10000,co2:30,social:9,dur:6}];
  var html="";
  defaults.forEach(function(d){
    html+="<div class='card'><div class='input-group'><label class='input-label'>Project Name</label><input class='input' value='"+d.name+"'></div>";
    html+="<div class='input-group'><label class='input-label'>Budget</label><input class='input' type='number' value='"+d.budget+"'></div>";
    html+="<div class='input-group'><label class='input-label'>CO2 Reduction</label><input class='input' type='number' value='"+d.co2+"'></div>";
    html+="<div class='input-group'><label class='input-label'>Social Impact</label><input class='input' type='number' value='"+d.social+"'></div>";
    html+="<div class='input-group'><label class='input-label'>Duration</label><input class='input' type='number' value='"+d.dur+"'></div></div>";
  });
  var ci=document.getElementById("compareInputs");if(ci)ci.innerHTML=html;
}
initCompare();
loadEvalHistory();

// PDF Report download
window.downloadPDF=function(){
  var d=getEvalData();
  fetch('/report/pdf',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})
  .then(function(r){return r.blob()}).then(function(b){
    var url=URL.createObjectURL(b);var a=document.createElement('a');
    a.href=url;a.download='SORA_Earth_Report.pdf';a.click();URL.revokeObjectURL(url);
  });
};

// Monte Carlo simulation
window.runMonteCarlo=function(){
  var d=getEvalData();
  fetch('/analytics/montecarlo',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({budget:d.budget,co2_reduction:d.co2_reduction,social_impact:d.social_impact,duration_months:d.duration_months})})
  .then(function(r){return r.json()}).then(function(mc){
    var msg='Monte Carlo (1000 simulations):\n';
    msg+='Mean: '+mc.mean_probability+'%\n';
    msg+='Median: '+mc.median_probability+'%\n';
    msg+='Std Dev: '+mc.std+'%\n';
    msg+='5th percentile: '+mc.p5+'%\n';
    msg+='95th percentile: '+mc.p95+'%';
    alert(msg);
  });
};

// Model comparison
window.compareModels=function(){
  var d=getEvalData();
  fetch('/analytics/model-compare',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({budget:d.budget,co2_reduction:d.co2_reduction,social_impact:d.social_impact,duration_months:d.duration_months})})
  .then(function(r){return r.json()}).then(function(mc){
    var msg='Model Comparison:\n';
    for(var k in mc.models){msg+=k+': '+mc.models[k].probability+'% ('+((mc.models[k].prediction===1)?'Success':'Fail')+')\n';}
    msg+='\nBest: '+mc.best_model;
    alert(msg);
  });
};

});

fetch("/api/v1/countries").then(function(r){return r.json()}).then(function(c){
  var sel=document.getElementById("e-country");if(!sel)return;
  sel.innerHTML="";
  var countries=Object.keys(c).sort();
  countries.forEach(function(name){
    var opt=document.createElement("option");opt.value=name;opt.textContent=name;
    if(name==="Germany")opt.selected=true;
    sel.appendChild(opt);
  });
});

window.monteCarlo=function(){
  var d={budget:+document.getElementById("e-budget").value,co2_reduction:+document.getElementById("e-co2").value,social_impact:+document.getElementById("e-social").value,duration_months:+document.getElementById("e-duration").value};
  fetch("/analytics/montecarlo",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)}).then(function(r){return r.json()}).then(function(j){
    var el=document.getElementById("monteCarloResult");if(el){el.style.display="block";
    var html="<div style='font-weight:700;margin-bottom:12px'>Monte Carlo ("+j.simulations+" runs)</div>";
    html+="<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:12px'>";
    html+="<div style='text-align:center;padding:16px;background:var(--bg);border-radius:10px'><div style='color:var(--sub);font-size:12px'>Mean</div><div style='font-size:24px;font-weight:700;color:var(--accent)'>"+(j.mean_probability||j.mean||"-")+"</div></div>";
    html+="<div style='text-align:center;padding:16px;background:var(--bg);border-radius:10px'><div style='color:var(--sub);font-size:12px'>Std</div><div style='font-size:24px;font-weight:700;color:#f59e0b'>"+(j.std_probability||j.std||"-")+"</div></div>";
    html+="<div style='text-align:center;padding:16px;background:var(--bg);border-radius:10px'><div style='color:var(--sub);font-size:12px'>95% CI</div><div style='font-size:24px;font-weight:700;color:#00c9db'>"+(j.p5||j.confidence_interval_low||"-")+" - "+(j.p95||j.confidence_interval_high||"-")+"</div></div>";
    html+="</div>";el.innerHTML=html}
  }).catch(function(e){console.error("mc",e)});
};

window.exportPDF=function(){
  var d={name:document.getElementById("e-name").value,budget:+document.getElementById("e-budget").value,co2_reduction:+document.getElementById("e-co2").value,social_impact:+document.getElementById("e-social").value,duration_months:+document.getElementById("e-duration").value,region:document.getElementById("e-country").value};
  fetch("/report/pdf",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)}).then(function(r){
    var ct=r.headers.get("content-type")||"";
    if(ct.indexOf("pdf")>=0||ct.indexOf("octet")>=0){return r.blob().then(function(b){var u=URL.createObjectURL(b);var a=document.createElement("a");a.href=u;a.download="report.pdf";a.click()})}
    return r.json().then(function(j){var w=window.open();w.document.write("<pre>"+JSON.stringify(j,null,2)+"</pre>")});
  }).catch(function(e){console.error("pdf",e)});
};

window.compareAllModels=function(){
  var d={budget:+document.getElementById("e-budget").value,co2_reduction:+document.getElementById("e-co2").value,social_impact:+document.getElementById("e-social").value,duration_months:+document.getElementById("e-duration").value};
  fetch("/analytics/model-compare",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)}).then(function(r){return r.json()}).then(function(j){
    var el=document.getElementById("compareModelsResult");if(el){el.style.display="block";
    var models=j.models||j;var html="<div style='font-weight:700;margin-bottom:12px'>Model Comparison</div><table style='width:100%;border-collapse:collapse'><thead><tr><th style='text-align:left;padding:8px;border-bottom:1px solid var(--border)'>Model</th><th style='padding:8px;border-bottom:1px solid var(--border)'>Prob</th><th style='padding:8px;border-bottom:1px solid var(--border)'>Result</th></tr></thead><tbody>";
    if(Array.isArray(models)){models.forEach(function(m){html+="<tr><td style='padding:8px;border-bottom:1px solid var(--border)'>"+m.model+"</td><td style='padding:8px;text-align:center;border-bottom:1px solid var(--border)'>"+m.probability.toFixed(1)+"%</td><td style='padding:8px;text-align:center;border-bottom:1px solid var(--border)'>"+(m.prediction===1?"Success":"Fail")+"</td></tr>"})}
    html+="</tbody></table>";el.innerHTML=html}
  }).catch(function(e){console.error("compare",e)});
};

fetch("/api/v1/countries").then(function(r){return r.json()}).then(function(c){
  var sel=document.getElementById("e-country");if(sel){sel.innerHTML="";
  Object.keys(c).sort().forEach(function(n){var o=document.createElement("option");o.value=n;o.textContent=n;if(n==="Germany")o.selected=true;sel.appendChild(o)})}
});
