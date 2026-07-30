import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { errorMessage } from "@/lib/errors";

type Sub = { id:number; url:string; event_type:string; active:boolean; created_at:string };
type Del = { id:number; subscription_id:number; event_type:string; status_code:number; ok:boolean; error:string; created_at:string };
const API = "/api/v1/webhooks";

/** What the server said, when it said no. */
async function failureText(r: Response): Promise<string> {
  try {
    const body = await r.json();
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string" && detail) return detail;
  } catch {
    // Not JSON, or no body at all: the status is the whole story.
  }
  return `request failed with ${r.status}`;
}

/**
 * fetch, but a non-2xx is a failure.
 *
 * fetch only rejects on a transport error. A 4xx or 5xx arrives as a resolved
 * response, and .json() on the `{"detail": ...}` body succeeds -- so unchecked,
 * that body became the query's data and `subs.map` crashed the render, while the
 * POST read `secret` off it, got undefined, cleared the form and refetched.
 * Which is exactly what success looked like.
 */
async function expectOk(input: string, init?: RequestInit): Promise<Response> {
  const r = await fetch(input, init);
  if (!r.ok) throw new Error(await failureText(r));
  return r;
}

export default function WebhooksPage() {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState<string|null>(null);
  const [problem, setProblem] = useState<string|null>(null);

  const { data: subs = [], error: subsError } = useQuery<Sub[]>({
    queryKey: ["webhooks", "subscriptions"],
    queryFn: async () => (await expectOk(API)).json(),
  });
  const { data: dels = [], error: delsError } = useQuery<Del[]>({
    queryKey: ["webhooks", "deliveries"],
    queryFn: async () => (await expectOk(API + "/deliveries")).json(),
  });

  const reload = () => qc.invalidateQueries({ queryKey: ["webhooks"] });

  // An empty list and a failed request look identical on screen, which is the
  // same fault in a different place -- so a failure says so.
  const loadProblem = subsError || delsError
    ? errorMessage(subsError ?? delsError, "could not load webhooks")
    : null;

  const add = async () => {
    setProblem(null);
    try {
      const r = await expectOk(API, { method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ url, event_type:"drift" }) });
      const d = await r.json();
      setSecret(typeof d?.secret === "string" ? d.secret : null);
      setUrl("");
      reload();
    } catch (e) {
      // The url stays in the box: it was not accepted, and retyping it is work.
      setProblem(errorMessage(e, "could not create the subscription"));
    }
  };
  const del = async (id:number) => {
    setProblem(null);
    try {
      await expectOk(`${API}/${id}`, { method:"DELETE" });
      reload();
    } catch (e) {
      setProblem(errorMessage(e, "could not delete the subscription"));
    }
  };

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
      {problem && <p role="alert" style={{color:"#d50000",fontSize:13}}>{problem}</p>}
      {loadProblem && <p role="alert" style={{color:"#d50000",fontSize:13}}>Could not load webhooks: {loadProblem}</p>}

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
