import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { errorMessage } from "@/lib/errors";
import { api, ApiError } from "@/api/client";
import { isSessionEnded } from "@/lib/session";

type Sub = { id:number; url:string; event_type:string; active:boolean; created_at:string };
type Del = { id:number; subscription_id:number; event_type:string; status_code:number; ok:boolean; error:string; created_at:string };
const API = "/webhooks";

/** What the server said, when it said no: its detail, or the status. */
function failureText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    const detail = (e.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string" && detail) return detail;
    return `request failed with ${e.status}`;
  }
  return errorMessage(e, fallback);
}

/**
 * What a list shows before the server has answered: loading, or a failure.
 * Neither is "none", so neither may read as an empty list.
 */
function NoAnswer({ what, failed }: { what: string; failed: boolean }) {
  return (
    <p style={{color:"var(--muted)"}}>
      {failed ? `${what} unavailable: the request failed.` : `Loading ${what.toLowerCase()}…`}
    </p>
  );
}

export default function WebhooksPage() {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState<string|null>(null);
  const [problem, setProblem] = useState<string|null>(null);
  // The secret lives only in this component: a session change unmounts it, and
  // an answer that arrives after that is refused by the transport.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // `data` stays undefined until the server answers. Defaulting it to [] made
  // "no answer yet" and "the server has none" the same empty list, so a request
  // still in flight, or one that failed, printed "No subscriptions yet".
  const subsQuery = useQuery<Sub[]>({
    queryKey: ["webhooks", "subscriptions"],
    // The shared transport: a non-2xx is an ApiError, never a body read as data.
    queryFn: ({ signal }) => api<Sub[]>(API, { signal }),
  });
  const delsQuery = useQuery<Del[]>({
    queryKey: ["webhooks", "deliveries"],
    queryFn: ({ signal }) => api<Del[]>(API + "/deliveries", { signal }),
  });
  const subs = subsQuery.data;
  const dels = delsQuery.data;
  const subsError = subsQuery.error;
  const delsError = delsQuery.error;

  const reload = () => qc.invalidateQueries({ queryKey: ["webhooks"] });

  // An empty list and a failed request look identical on screen, which is the
  // same fault in a different place -- so a failure says so.
  const loadProblem = subsError || delsError
    ? ((subsError instanceof ApiError && subsError.status === 401) ||
       (delsError instanceof ApiError && delsError.status === 401)
        ? "Sign in to view webhook subscriptions"
        : failureText(subsError ?? delsError, "could not load webhooks"))
    : null;

  const add = async () => {
    setProblem(null);
    try {
      const d = await api<{ secret?: unknown }>(API, { method:"POST",
        body: JSON.stringify({ url, event_type:"drift" }) });
      if (!mounted.current) return;
      setSecret(typeof d?.secret === "string" ? d.secret : null);
      setUrl("");
      reload();
    } catch (e) {
      if (isSessionEnded(e) || !mounted.current) return;
      // The url stays in the box: it was not accepted, and retyping it is work.
      setProblem(failureText(e, "could not create the subscription"));
    }
  };
  const del = async (id:number) => {
    setProblem(null);
    try {
      await api(`${API}/${id}`, { method:"DELETE" });
      if (mounted.current) reload();
    } catch (e) {
      if (isSessionEnded(e) || !mounted.current) return;
      setProblem(failureText(e, "could not delete the subscription"));
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
      {subs === undefined ? <NoAnswer what="Subscriptions" failed={!!subsError} /> :
       subs.length===0 ? <p style={{color:"var(--muted)"}}>No subscriptions yet</p> :
        <table style={{width:"100%",fontSize:13}}><thead><tr><th>URL</th><th>Event</th><th>Active</th><th></th></tr></thead>
        <tbody>{subs.map(s=>(<tr key={s.id}><td>{s.url}</td><td>{s.event_type}</td><td>{s.active?"yes":"no"}</td>
          <td><button onClick={()=>del(s.id)} style={{color:"#d50000",background:"none",border:"none",cursor:"pointer"}}>delete</button></td></tr>))}</tbody></table>}

      <h3 style={{marginTop:24}}>Recent deliveries</h3>
      {dels === undefined ? <NoAnswer what="Deliveries" failed={!!delsError} /> :
       dels.length===0 ? <p style={{color:"var(--muted)"}}>No deliveries yet</p> :
        <table style={{width:"100%",fontSize:13}}><thead><tr><th>Sub</th><th>Event</th><th>Status</th><th>OK</th><th>When</th></tr></thead>
        <tbody>{dels.map(d=>(<tr key={d.id}><td>{d.subscription_id}</td><td>{d.event_type}</td>
          <td>{d.status_code??"—"}</td><td>{d.ok?"✓":"✗"}</td><td>{new Date(d.created_at).toLocaleString()}</td></tr>))}</tbody></table>}
    </div>
  );
}
