const routes = {
  "/admin": "evaluate",
  "/admin/": "evaluate",
  "/admin/evaluate": "evaluate",
  "/admin/dashboard": "dashboard",
  "/admin/country-ranking": "country-ranking",
  "/admin/monte-carlo": "monte-carlo",
  "/admin/snapshot": "snapshot",
  "/admin/ai-teammate": "ai-teammate",
  "/admin/system-summary": "system-summary",
  "/admin/retrain-log": "retrain-log",
  "/admin/explainability": "explainability",
  "/admin/diagnostics": "diagnostics",
  "/admin/activity-timeline": "activity-timeline",
  "/admin/audit-log": "audit-log",
  "/admin/data-health": "data-health",
  "/admin/predictions-log": "predictions-log",
  "/admin/batch-evaluations": "batch-evaluations"
};

async function loadView() {
  const path = location.pathname.replace(/\/$/, "") || "/admin";
  const view = routes[path] || "evaluate";
  const v = Date.now();
  let html;
  try {
    const r = await fetch("/static/views/" + view + ".html?v=" + v);
    html = await r.text();
  } catch (e) {
    html = '<div class="page-head"><div class="page-title">Not found</div></div>';
  }
  document.getElementById("outlet").innerHTML = html;
  document.querySelectorAll(".sidebar-item").forEach(function (a) {
    a.classList.toggle("active", a.getAttribute("href") === path);
  });
  try {
    const m = await import("/static/js/views/" + view + ".js?v=" + v);
    if (m && m.mount) m.mount();
  } catch (e) {
    console.warn("view load failed:", e);
  }
}

window.addEventListener("popstate", loadView);
document.addEventListener("click", function (e) {
  const a = e.target.closest("a[data-spa]");
  if (!a) return;
  e.preventDefault();
  history.pushState({}, "", a.href);
  loadView();
});
loadView();
