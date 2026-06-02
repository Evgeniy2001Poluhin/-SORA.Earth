import { useEffect, useState } from "react";

type Sub = { id:number; url:string; event_type:string; active:boolean; created_at:string };
type Del = { id:number; subscription_id:number; event_type:string; status_code:number; ok:boolean; error:string; created_at:string };
const API = "/api/v1/webhooks";

export default function WebhooksPage() {
  const [subs, setSubs] = useState<Sub[]>([]);
  const [dels, setDels] = useState<Del[]>([]);
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState<string|null>(null);

  const load = async () => {
    setSubs(await (await fetch(API)).json());
    setDels(await (await fetch(API+"/deliveries")).json());
  };
  useEffect(() => { load(); }, []);

  const add = async () => {
    const r = await fetch(API, { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ url, event_type:"drift" }) });
    const d = await r.json(); setSecret(d.secret); setUrl(""); load();
  };
  const del = async (id:number) => { await fetch(`${API}/${id}`, { method:"DELETE" }); load(); };

  return (
    <div className="webhooks-page" style={{padding:24}}>
      <div className="eyebrow">B2B • Drift Event Webhooks</div>
      <h1>Webhook Subscriptions</h1>
      <div style={{display:"flex",gap:8,margin:"16px 0"}}>
        <input value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://your-endpoint.com/hook"
          style={{flex:1,padding:10,borderRadius:8,background:"var(--bg)",color:"var(--text)",border:"1px solid var(--line-2)"}}/>
        <button className="btn-primary" onClick={add} disabled={!url}>Subscribe</button>
      </div>
      {secret && <p style={{color:"var(--planet)",fontSize:13}}>Secret (save it, shown once): <code>{secret}</code></p>}

      <h3>Active subscriptions</h3>
      {subs.length===0 ? <p style={{color:"var(--muted)"}}>No subscriptions yet</p> :
        <table style={{width:"100%",fontSize:13}}><thead><tr><th>URL</th><th>Event</th><th>Active</th><th></th></tr></thead>
        <tbody>{subs.map(s=>(<tr key={s.id}><td>{s.url}</td><td>{s.event_type}</td><td>{s.active?"yes":"no"}</td>
          <td><button onClick={()=>del(s.id)} style={{color:"#d50000",background:"none",border:"none",cursor:"pointer"}}>delete</button></td></tr>))}</tbody></table>}

      <h3 style={{marginTop:24}}>Recent deliveries</h3>
      {dels.length===0 ? <p style={{color:"var(--muted)"}}>No deliveries yet</p> :
        <table style={{width:"100%",fontSize:13}}><thead><tr><th>Sub</th><th>Event</th><th>Status</th><th>OK</th><th>When</th></tr></thead>
        <tbody>{dels.map(d=>(<tr key={d.id}><td>{d.subscription_id}</td><td>{d.event_type}</td>
          <td>{d.status_code??"—"}</td><td>{d.ok?"✓":"✗"}</td><td>{new Date(d.created_at).toLocaleString()}</td></tr>))}</tbody></table>}
    </div>
  );
}
