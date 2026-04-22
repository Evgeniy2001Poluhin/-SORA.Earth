// fetch() shim — auto-attach JWT
(function() {
  var origFetch = window.fetch.bind(window);
  window.fetch = function(url, opts) {
    opts = opts || {};
    var u = typeof url === "string" ? url : (url && url.url) || "";
    if (u.indexOf("/api/") === 0 || u.indexOf("http://localhost/api/") === 0) {
      var tok = localStorage.getItem("sora_jwt");
      if (tok) opts.headers = Object.assign({}, opts.headers || {}, {Authorization: "Bearer " + tok});
    }
    return origFetch(url, opts);
  };
})();

(function() {
  var tok = localStorage.getItem("sora_jwt");
  var path = location.pathname;
  if (!tok && path.indexOf("/app/") === 0) {
    location.href = "/auth/login?return=" + encodeURIComponent(path + location.search);
  }
})();

var LEGACY = ["evaluate","dashboard","country-ranking","monte-carlo","snapshot","ai-teammate","system-summary","retrain-log","explainability","diagnostics","activity-timeline","audit-log","data-health","predictns-log","batch-evaluations","shap"];
var NEW_PAGES = ["home","evaluate-page","insights","operations"];

var PAGE_TABS = {};
PAGE_TABS["home"] = [{id:"system-summary",label:"Overview"},{id:"activity-timeline",label:"Activity"}];
PAGE_TABS["evaluate-page"] = [{id:"evaluate",label:"Project"},{id:"country-ranking",label:"Country ranking"},{id:"monte-carlo",label:"Monte Carlo"},{id:"snapshot",label:"Snapshot"}];
PAGE_TABS["insights"] = [{id:"dashboard",label:"Dashboard"},{id:"explainability",label:"Explainability"},{id:"predictions-log",label:"Predictions log"},{id:"shap",label:"SHAP"}];
PAGE_TABS["operations"] = [{id:"ai-teammate",label:"AI Teammate"},{id:"retrain-log",label:"Retrain log"},{id:"batch-evaluations",label:"Batch jobs"},{id:"audit-log",label:"Audit"},{id:"data-health",label:"Data health"},{id:"diagnostics",label:"Diagnostics"}];

var HERO = {};
HERO["home"] = {e:"WELCOME BACK", h:"Your ESG platform is healthy", s:"Live metrics"};
// page marker injected by patch
HERO["evaluate-page"] = {e:"ML SCORING", h:"Evaluate an ESG project", s:"Score, compare, simulate."};
HERO["insights"] = {e:"ANALYTICS", h:"Insights and explainability", s:"Dashboard, SHAP, logs."};
HERO["operations"] = {e:"MLOPS", h:"Operations and audit", s:"AI teammate, retrain, audit."};

var routes = {};
LEGACY.forEach(function(v){ routes["/app/"+v] = v; });
NEW_PAGES.forEach(function(p){ routes["/app/"+p] = "__page:"+p; });
routes["/app"] = "__page:home";
routes["/app/"] = "__page:home";

function getQ(n){ return new URLSearchParams(location.search).get(n); }

async function mountLegacy(view, hostId) {
  try { document.body.dataset.page = (location.pathname.split('/').filter(Boolean).pop() || 'home'); } catch(e){}
  var host = document.getElementById(hostId) || document.getElementById("outlet");
  host.innerHTML = '<div id="c-'+view+'"></div>';
  try {
    var m = await import("/static/js/views/"+view+".js?v="+Date.now());
    if (m && m.mount) await m.mount();
  } catch(e) {
    console.error("mount failed", view, e);
    host.innerHTML = '<div style="padding:32px;color:#f87171">Failed to load <b>'+view+'</b><br><small>'+(e.message||e)+'</small></div>';
  }
}

async function renderPage(pid) {
  var outlet = document.getElementById("outlet");
  var tabs = PAGE_TABS[pid] || [];
  var activeTab = getQ("tab") || (tabs[0] && tabs[0].id);
  document.body.dataset.page = pid;
  var hero = HERO[pid] || HERO["home"];

  var tabsHtml = tabs.map(function(t){
    var border = (t.id===activeTab) ? "#5eead4" : "transparent";
    return '<div class="tab" data-tab="'+t.id+'" style="display:inline-block;padding:10px 18px;margin-right:8px;cursor:pointer;border-bottom:2px solid '+border+'">'+t.label+'</div>';
  }).join("");

// hidden breadcrumb stub for legacy views
  var _crumb = document.getElementById("crumb");
  if (!_crumb) { var cb = document.createElement("div"); cb.id="crumb"; cb.style.display="none"; document.body.appendChild(cb); }
  outlet.innerHTML =
    '<div style="background:linear-gradient(135deg,#0b1f2a,#082024);border-radius:16px;padding:40px;margin-bottom:24px">' +
      '<div style="font:600 11px/1 monospace;letter-spacing:2px;color:#5eead4;margin-bottom:12px">'+hero.e+'</div>' +
      '<h1 style="font-size:32px;margin:0 0 12px;color:#fff">'+hero.h+'</h1>' +
      '<p id="hero-live-sub" style="color:#94a3b8;margin:0">'+hero.s+'</p>' +
    '</div>' +
    '<div class="s-tabs" role="tablist">'+tabsHtml+'</div>' +
    '<div id="outlet-tab">Loading...</div>';

  outlet.querySelectorAll(".tab").forEach(function(el){
    el.addEventListener("click", function(){
      var t = el.dataset.tab;
      history.pushState({}, "", location.pathname+"?tab="+t);
      renderPage(pid);
    });
  });

  if (activeTab) await mountLegacy(activeTab, "outlet-tab");
}

async function loadView() {
  var path = location.pathname.replace(/\/$/, "") || "/app";
  var target = routes[path];
  if (target && target.indexOf("__page:") === 0) {
    await renderPage(target.replace("__page:",""));
  } else {
    await mountLegacy(target || "evaluate", "outlet");
  }
}

window.addEventListener("popstate", loadView);
document.addEventListener("click", function(e){
  var a = e.target.closest("a[data-spa]");
  if (!a) return;
  e.preventDefault();
  history.pushState({}, "", a.href);
  loadView();
});

loadView();
