import { useQuery } from "@tanstack/react-query";

// `ok` is null when the response did not carry a boolean for that component.
type Comp = { component: string; ok: boolean | null };
type Status = { overall: string; components: Comp[] };
type Tone = { bg: string; color: string };
type Headline = { text: string; detail?: string; tone: Tone };

const LABEL: Record<string, string> = { api: "API", models: "ML Models", database: "Database", external_data: "External Data" };
const REFRESH_MS = 30000;
const GREEN: Tone = { bg: "rgba(16,185,129,.12)", color: "var(--planet,#10b981)" };
const RED: Tone = { bg: "rgba(213,0,0,.12)", color: "#d50000" };
const GREY: Tone = { bg: "rgba(154,160,166,.12)", color: "var(--muted,#9aa0a6)" };

/**
 * Keeps only what the backend actually sent. A component whose `ok` is not a
 * boolean becomes `null` (unknown): a missing field, `0` or `"false"` is not a
 * measurement that the component is down (F054). A body with no components is
 * a failed answer, not an empty status that could read as all clear.
 */
function parseStatus(body: unknown): Status {
  const top = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  if (!Array.isArray(top.components) || top.components.length === 0) {
    throw new Error("status response has no components");
  }
  return {
    overall: typeof top.overall === "string" ? top.overall : "",
    components: top.components.map((raw): Comp => {
      const c = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
      return {
        component: typeof c.component === "string" && c.component ? c.component : "Unnamed component",
        ok: typeof c.ok === "boolean" ? c.ok : null,
      };
    }),
  };
}

async function fetchStatus(): Promise<Status> {
  const r = await fetch("/api/v1/status/uptime");
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return parseStatus(await r.json());
}

/**
 * What the banner may say. "Operational" needs a well-formed answer that says so
 * and no failed refresh since. A failed refresh never leaves the last green
 * answer on screen (F053), and a component that did not report is never read
 * as clear.
 */
function headlineFor(data: Status | undefined, refreshFailed: boolean, checkedAt: string): Headline {
  if (!data) {
    return refreshFailed
      ? { text: "Status temporarily unavailable", tone: GREY }
      : { text: "Checking…", tone: GREY };
  }
  if (refreshFailed) {
    return {
      text: "Current status unknown",
      detail: `The latest refresh failed. Last successful check: ${checkedAt}.`,
      tone: GREY,
    };
  }
  if (data.overall === "degraded" || data.components.some((c) => c.ok === false)) {
    return { text: "Degraded performance", tone: RED };
  }
  if (data.overall === "operational" && data.components.every((c) => c.ok === true)) {
    return { text: "All systems operational", tone: GREEN };
  }
  return {
    text: "Status incomplete",
    detail: "Part of the status response could not be read, so this page does not claim every component is operational.",
    tone: GREY,
  };
}

/** Rows from the last successful check are muted, so they do not read as live. */
function cellFor(ok: boolean | null, stale: boolean): { text: string; color: string } {
  if (ok === null) return { text: "● Unknown", color: "var(--muted)" };
  if (stale) return { text: ok ? "● Operational" : "● Down", color: "var(--muted)" };
  return ok
    ? { text: "● Operational", color: "var(--planet,#10b981)" }
    : { text: "● Down", color: "#d50000" };
}

export default function StatusPage() {
  // A status page has to survive the outage it exists to report. The checks catch
  // their own failures, so a database outage arrives as a 200 with `database`
  // down. A 500, or a body that is not a status, is a failed refresh (#236).
  //
  // TanStack keeps the last successful answer through a failed refetch, so `data`
  // alone would keep the green headline after a failure. `isError` is read beside
  // it (F053). `retry: false` because the 30-second refetch is the retry. Polling
  // continues in background tabs, as the old timer did, and `gcTime: 0` means every
  // visit starts from a fresh answer, never from a cached green one.
  const q = useQuery({
    queryKey: ["status", "uptime"],
    queryFn: fetchStatus,
    refetchInterval: REFRESH_MS,
    refetchIntervalInBackground: true,
    retry: false,
    gcTime: 0,
  });
  const data = q.data;
  const refreshFailed = q.isError;
  const checkedAt = q.dataUpdatedAt ? new Date(q.dataUpdatedAt).toLocaleString() : "an unknown time";
  const head = headlineFor(data, refreshFailed, checkedAt);

  return (
    <div style={{padding:24, maxWidth:760}}>
      <div className="eyebrow">SYSTEM • PUBLIC STATUS</div>
      <h1>Service Status</h1>
      <div style={{display:"inline-flex",alignItems:"center",gap:8,padding:"8px 14px",borderRadius:10,
        background: head.tone.bg, color: head.tone.color, fontWeight:600, margin:"8px 0 8px"}}>
        <span style={{width:10,height:10,borderRadius:"50%",background:"currentColor"}}/>
        {head.text}
      </div>
      {head.detail && <p style={{color:"var(--muted)",fontSize:13,margin:"0 0 16px"}}>{head.detail}</p>}
      {!data ? <p style={{color:"var(--muted)"}}>{refreshFailed ? "Could not reach the status service." : "Loading…"}</p> :
        <>
          {refreshFailed && <p style={{color:"var(--muted)",fontSize:13,margin:"0 0 8px"}}>Rows are from the last successful check.</p>}
          <table style={{width:"100%",fontSize:14,borderCollapse:"collapse"}}>
            <thead><tr style={{textAlign:"left",color:"var(--muted)"}}>
              <th style={{padding:"8px 0"}}>Component</th><th>Status</th>
            </tr></thead>
            <tbody>{data.components.map((c, i) => {
              const cell = cellFor(c.ok, refreshFailed);
              return (
                <tr key={`${c.component}-${i}`} style={{borderTop:"1px solid var(--line-2,#222)"}}>
                  <td style={{padding:"10px 0"}}>{LABEL[c.component] ?? c.component}</td>
                  <td style={{color: cell.color}}>{cell.text}</td>
                </tr>);
            })}
            </tbody>
          </table>
        </>}
      <p style={{color:"var(--muted)",fontSize:12,marginTop:16}}>Auto-refreshes every 30s</p>
    </div>
  );
}
