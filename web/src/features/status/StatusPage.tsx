import { useEffect, useState } from "react";

type Comp = { component:string; ok:boolean; uptime_24h:number|null; uptime_7d:number|null };
type Data = { overall:string; components:Comp[] };
const LABEL:Record<string,string> = { api:"API", models:"ML Models", database:"Database", external_data:"External Data" };

export default function StatusPage() {
  const [d, setD] = useState<Data|null>(null);
  useEffect(() => {
    const load = () => fetch("/api/v1/status/uptime").then(r=>r.json()).then(setD).catch(()=>{});
    load(); const t = setInterval(load, 30000); return () => clearInterval(t);
  }, []);
  const op = d?.overall === "operational";
  return (
    <div style={{padding:24, maxWidth:760}}>
      <div className="eyebrow">SYSTEM • PUBLIC STATUS</div>
      <h1>Service Status</h1>
      <div style={{display:"inline-flex",alignItems:"center",gap:8,padding:"8px 14px",borderRadius:10,
        background: op ? "rgba(16,185,129,.12)" : "rgba(213,0,0,.12)",
        color: op ? "var(--planet,#10b981)" : "#d50000", fontWeight:600, margin:"8px 0 20px"}}>
        <span style={{width:10,height:10,borderRadius:"50%",background:"currentColor"}}/>
        {op ? "All systems operational" : "Degraded performance"}
      </div>
      {!d ? <p style={{color:"var(--muted)"}}>Loading…</p> :
        <table style={{width:"100%",fontSize:14,borderCollapse:"collapse"}}>
          <thead><tr style={{textAlign:"left",color:"var(--muted)"}}>
            <th style={{padding:"8px 0"}}>Component</th><th>Status</th><th>Uptime 24h</th><th>Uptime 7d</th>
          </tr></thead>
          <tbody>{d.components.map(c=>(
            <tr key={c.component} style={{borderTop:"1px solid var(--line-2,#222)"}}>
              <td style={{padding:"10px 0"}}>{LABEL[c.component] ?? c.component}</td>
              <td style={{color: c.ok ? "var(--planet,#10b981)" : "#d50000"}}>{c.ok ? "● Operational" : "● Down"}</td>
        <td>{c.uptime_24h==null ? "—" : c.uptime_24h + "%"}</td>
              <td>{c.uptime_7d==null ? "—" : c.uptime_7d + "%"}</td>
            </tr>))}
          </tbody>
        </table>}
      <p style={{color:"var(--muted)",fontSize:12,marginTop:16}}>Auto-refreshes every 30s · uptime sampled every 5 min</p>
    </div>
  );
}
