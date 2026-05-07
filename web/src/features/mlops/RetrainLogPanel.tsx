import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { adminApi } from "@/api/endpoints/admin";
import { useAuth } from "@/store/auth";

export function RetrainLogPanel() {
  const user = useAuth((s) => s.user);
  const enabled = !!user;
  const q = useQuery({
    queryKey: ["admin-retrain-log"],
    queryFn: adminApi.retrainLog,
    enabled,
    refetchInterval: 30000,
  });

  if (!enabled) {
    return (
      <div className="kpi locked-tile" style={{ pointerEvents: "auto", opacity: 1 }}>
        <div className="locked-badge">Sign-in required</div>
        <div className="kpi-lbl">Admin retrain log</div>
        <div className="kpi-val" style={{ fontSize: 13 }}>
          <Link to="/login" style={{ color: "#2FE0A6" }}>Sign in</Link>
        </div>
      </div>
    );
  }
  if (q.isLoading) return <div className="card-body"><p style={{ color: "var(--muted)" }}>Loading retrain log...</p></div>;
  if (q.isError) return <div className="card-body"><p style={{ color: "#EF4444" }}>Failed to load retrain log</p></div>;

  const items = (q.data && q.data.items ? q.data.items : []).slice(0, 15);
  const total = q.data && q.data.items ? q.data.items.length : 0;

  return (
    <div>
      <div className="eyebrow" style={{ marginBottom: 12 }}>Retrain log: {total} entries</div>
      <div className="mlops-table">
        <div className="mlops-row mlops-head">
          <div>Version</div>
          <div>Trigger</div>
          <div className="tabular">Dur</div>
          <div className="tabular">Acc</div>
          <div className="tabular">F1</div>
          <div className="tabular">AUC</div>
          <div>Status</div>
        </div>
        {items.map((r) => {
          let mx: { accuracy?: number; f1_score?: number; roc_auc?: number } = {};
          try { mx = r.metrics_json ? JSON.parse(r.metrics_json) : {}; } catch (e) { mx = {}; }
          const ok = r.status === "success";
          const bgOk = "rgba(47,224,166,0.12)";
          const bgBad = "rgba(239,68,68,0.12)";
          return (
            <div key={r.id} className="mlops-row">
              <div className="mono" style={{ fontSize: 11 }}>{r.model_version || "-"}</div>
              <div style={{ color: "var(--muted)", fontSize: 12 }}>{r.trigger_source}</div>
              <div className="tabular">{r.duration_sec.toFixed(1)}s</div>
              <div className="tabular">{mx.accuracy !== undefined ? mx.accuracy.toFixed(3) : "-"}</div>
              <div className="tabular">{mx.f1_score !== undefined ? mx.f1_score.toFixed(3) : "-"}</div>
              <div className="tabular">{mx.roc_auc !== undefined ? mx.roc_auc.toFixed(3) : "-"}</div>
              <div>
                <span className="pill" style={{ color: ok ? "#2FE0A6" : "#EF4444", background: ok ? bgOk : bgBad }}>{r.status}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}