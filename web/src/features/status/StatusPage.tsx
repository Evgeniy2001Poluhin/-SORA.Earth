import { useEffect, useState } from "react";

type Comp = {
  component:string; ok:boolean;
  uptime_24h:number|null; uptime_7d:number|null;
  coverage_24h?:number|null; coverage_7d?:number|null;
};
type Data = { overall:string; components:Comp[]; sample_interval_minutes?:number };

// What the samples support, and no more.
//
// `uptime_*` is a lower bound: an interval nobody sampled counts against it.
// The recorder runs inside the process being measured, so an outage leaves no
// ping rather than a failing one, and a percentage over the pings that exist
// cannot see it -- measured, six unrecorded hours of a day read as 100%.
//
// A lower bound shown alone has the opposite failure. The scheduler container
// stopped claiming `api`, which it cannot observe, so those rows now arrive
// only when somebody opens this page: a healthy API would read 0.7%. So the
// range is shown. An unsampled interval could have gone either way, which puts
// the truth between `pct` and `pct + (100 - coverage)`, and on a fully sampled
// window the two ends meet and one number is printed.
function Uptime({ pct, coverage }: { pct:number|null; coverage?:number|null }) {
  if (pct == null) return <>&mdash;</>;
  const unknown = coverage == null ? 0 : Math.round((100 - coverage) * 100) / 100;
  if (unknown <= 0) return <>{pct}%</>;
  const high = Math.round(Math.min(100, pct + unknown) * 100) / 100;
  return (
    <>
      {pct}&ndash;{high}%
      <span style={{color:"var(--muted,#9aa0a6)",fontSize:12,marginLeft:6}}>
        ({coverage}% observed)
      </span>
    </>
  );
}
const LABEL:Record<string,string> = { api:"API", models:"ML Models", database:"Database", external_data:"External Data" };

export default function StatusPage() {
  const [d, setD] = useState<Data|null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    // A status page has to survive the outage it exists to report. The endpoint
    // (status_summary) has no `except`, so a database error is a 500 -- and a
    // 500 body is `{detail: ...}`, not a status. Checking `r.ok` and requiring
    // a real `components` array keeps that error body out of the state, which
    // used to pass `!d` and crash the page on `d.components.map` (#236).
    const load = () => fetch("/api/v1/status/uptime")
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((body) => {
        if (body && Array.isArray(body.components)) { setD(body); setFailed(false); }
        else { setFailed(true); }
      })
      .catch(() => setFailed(true));
    load(); const t = setInterval(load, 30000); return () => clearInterval(t);
  }, []);
  // "operational" only when a well-formed answer says so; otherwise degraded,
  // and "unavailable" (neutral) when nothing has loaded yet or the last fetch
  // failed -- never a green banner over an unknown state.
  const state: "up" | "degraded" | "unavailable" =
    d ? (d.overall === "operational" ? "up" : "degraded") : "unavailable";
  const banner = state === "up"
    ? { text: "All systems operational", bg: "rgba(16,185,129,.12)", color: "var(--planet,#10b981)" }
    : state === "degraded"
      ? { text: "Degraded performance", bg: "rgba(213,0,0,.12)", color: "#d50000" }
      : { text: failed ? "Status temporarily unavailable" : "Checking…", bg: "rgba(154,160,166,.12)", color: "var(--muted,#9aa0a6)" };
  return (
    <div style={{padding:24, maxWidth:760}}>
      <div className="eyebrow">SYSTEM • PUBLIC STATUS</div>
      <h1>Service Status</h1>
      <div style={{display:"inline-flex",alignItems:"center",gap:8,padding:"8px 14px",borderRadius:10,
        background: banner.bg, color: banner.color, fontWeight:600, margin:"8px 0 20px"}}>
        <span style={{width:10,height:10,borderRadius:"50%",background:"currentColor"}}/>
        {banner.text}
      </div>
      {!d ? <p style={{color:"var(--muted)"}}>{failed ? "Could not reach the status service." : "Loading…"}</p> :
        <table style={{width:"100%",fontSize:14,borderCollapse:"collapse"}}>
          <thead><tr style={{textAlign:"left",color:"var(--muted)"}}>
            <th style={{padding:"8px 0"}}>Component</th><th>Status</th><th>Uptime 24h</th><th>Uptime 7d</th>
          </tr></thead>
          <tbody>{d.components.map(c=>(
            <tr key={c.component} style={{borderTop:"1px solid var(--line-2,#222)"}}>
              <td style={{padding:"10px 0"}}>{LABEL[c.component] ?? c.component}</td>
              <td style={{color: c.ok ? "var(--planet,#10b981)" : "#d50000"}}>{c.ok ? "● Operational" : "● Down"}</td>
              <td><Uptime pct={c.uptime_24h} coverage={c.coverage_24h}/></td>
              <td><Uptime pct={c.uptime_7d} coverage={c.coverage_7d}/></td>
            </tr>))}
          </tbody>
        </table>}
      {/* The cadence comes from the backend, which owns it: written here as a
          literal it would go on claiming five minutes after the job changed. */}
      <p style={{color:"var(--muted)",fontSize:12,marginTop:16}}>
        Auto-refreshes every 30s
        {d?.sample_interval_minutes ? ` · uptime sampled every ${d.sample_interval_minutes} min` : ""}
      </p>
      <p style={{color:"var(--muted)",fontSize:12,marginTop:4}}>
        A range means part of the window holds no sample and could have gone
        either way; "observed" is how much of it was sampled at all.
      </p>
    </div>
  );
}
