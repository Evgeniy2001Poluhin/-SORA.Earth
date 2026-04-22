export async function api(url, opts = {}) {
  const tok = localStorage.getItem("sora_jwt");
  const headers = Object.assign(
    {"Content-Type": "application/json"},
    tok ? {Authorization: "Bearer " + tok} : {"X-API-Key": "super-secret-demo-token"},
    opts.headers || {}
  );
  const r = await fetch(url, Object.assign({}, opts, {headers}));
  if (!r.ok) throw new Error("HTTP " + r.status + " " + r.statusText);
  return r.json();
}
