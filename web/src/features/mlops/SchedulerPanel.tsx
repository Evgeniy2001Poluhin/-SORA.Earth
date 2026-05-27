import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { schedulerApi } from "@/api/endpoints/scheduler";
import { useAuth } from "@/store/auth";

export function SchedulerPanel() {
  const user = useAuth((s) => s.user);
  const enabled = !!user;
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string>("");

  const status = useQuery({ queryKey: ["sched-status"], queryFn: schedulerApi.status, enabled, refetchInterval: 15000 });
  const history = useQuery({ queryKey: ["sched-hist"], queryFn: schedulerApi.history, enabled, refetchInterval: 30000 });

  const trigger = useMutation({ mutationFn: schedulerApi.trigger, onSuccess: () => { setMsg("OK Retrain triggered"); qc.invalidateQueries({ queryKey: ["sched-status"] }); qc.invalidateQueries({ queryKey: ["sched-hist"] }); }, onError: () => setMsg("ERR Trigger failed") });
  const refresh = useMutation({ mutationFn: schedulerApi.refreshExternal, onSuccess: () => setMsg("OK ESG refresh queued"), onError: () => setMsg("ERR Refresh failed") });

  if (!enabled) return (<div className="kpi"><div className="kpi-lbl">Scheduler</div><div className="kpi-val" style={{ fontSize: 13 }}>Sign in required</div></div>);

  const s = status.data;
  const items = (history.data ?? []).slice(0, 10);

  return (
    <div>
      <div className="eyebrow" style={{ marginBottom: 12 }}>Scheduler runs: {s?.retrain_history_count ?? 0}</div>
      <div className="kpi-grid" style={{ marginBottom: 16 }}>
        <div className="kpi"><div className="kpi-lbl">Status</div><div className="kpi-val" style={{ fontSize: 18, color: s?.running ? "var(--planet)" : "var(--muted)" }}>{s?.running ? "RUNNING" : "IDLE"}</div></div>
        <div className="kpi"><div className="kpi-lbl">Jobs</div><div className="kpi-val tabular">{s?.jobs?.length ?? 0}</div></div>
        <div className="kpi"><div className="kpi-lbl">Enabled</div><div className="kpi-val" style={{ fontSize: 18 }}>{s?.enabled ? "YES" : "NO"}</div></div>
        <div className="kpi"><div className="kpi-lbl">History</div><div className="kpi-val tabular">{s?.retrain_history_count ?? 0}</div></div>
      </div>
      <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
        <button disabled={trigger.isPending} onClick={() => trigger.mutate()} style={{ padding: "10px 16px", background: "var(--planet)", color: "var(--bg)", border: "none", borderRadius: 8, fontWeight: 600, cursor: "pointer" }}>{trigger.isPending ? "..." : "Trigger retrain"}</button>
        <button disabled={refresh.isPending} onClick={() => refresh.mutate()} style={{ padding: "10px 16px", background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 8, fontWeight: 600, cursor: "pointer" }}>{refresh.isPending ? "..." : "Refresh ESG"}</button>
      </div>
      {msg && (<div style={{ marginBottom: 16, color: msg.startsWith("OK") ? "var(--planet)" : "var(--danger, #EF4444)" }}>{msg}</div>)}
      <div className="mlops-table">
        <div className="mlops-row mlops-head"><div>Started</div><div>Job</div><div>Trigger</div><div className="tabular">Dur</div><div>Version</div><div>Status</div></div>
        {items.length === 0 && (<div className="mlops-row"><div style={{ color: "var(--muted)" }}>No history yet</div></div>)}
        {items.map((r) => (
          <div key={r.id} className="mlops-row">
            <div style={{ fontSize: 11, color: "var(--muted)" }}>{new Date(r.started_at).toLocaleString()}</div>
            <div className="mono" style={{ fontSize: 11 }}>{r.job_name}</div>
            <div style={{ color: "var(--muted)", fontSize: 12 }}>{r.trigger_source}</div>
            <div className="tabular">{r.duration_sec != null ? r.duration_sec.toFixed(1) + "s" : "-"}</div>
            <div className="mono" style={{ fontSize: 11 }}>{r.model_version ?? "-"}</div>
            <div><span className="pill" style={{ color: r.status === "success" ? "var(--planet)" : "var(--danger, #EF4444)", background: r.status === "success" ? "rgba(47,224,166,0.12)" : "rgba(239,68,68,0.12)" }}>{r.status}</span></div>
          </div>
        ))}
      </div>
    </div>
  );
}
