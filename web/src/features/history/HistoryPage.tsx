import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { historyApi } from "../../api/endpoints/history";
import type { HistoryItem, HistoryParams } from "../../api/endpoints/history";
import "./history.css";

const PAGE = 20;
const RISKS: Array<"LOW" | "MED" | "HIGH"> = ["LOW", "MED", "HIGH"];

export default function HistoryPage() {
  const nav = useNavigate();
  const [params, setParams] = useState<HistoryParams>({ limit: PAGE, offset: 0 });
  const [data, setData] = useState<{ items: HistoryItem[]; total: number }>({ items: [], total: 0 });
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    historyApi
      .list(params)
      .then((r) => setData({ items: r.items, total: r.total }))
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [params]);

  const page = Math.floor((params.offset || 0) / PAGE) + 1;
  const pages = Math.max(1, Math.ceil(data.total / PAGE));
  const set = (patch: Partial<HistoryParams>) => setParams((p) => ({ ...p, ...patch, offset: 0 }));

  const fmt = (s: string) =>
    new Date(s).toLocaleString("en-GB", {
      year: "2-digit", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });

  const avgScore = useMemo(
    () => (data.items.length ? data.items.reduce((a, x) => a + x.total_score, 0) / data.items.length : 0),
    [data.items]
  );

  return (
    <div className="hist-page">
      <div className="eyebrow">EVALUATION LOG</div>
      <h1 className="hist-title">History</h1>
      <div className="ev-meta">
        <span>{data.total} evaluations</span>
        <span>·</span>
        <span>avg score {avgScore.toFixed(1)}</span>
        <span>·</span>
        <span>page {page} of {pages}</span>
      </div>

      <div className="hist-filters">
        <input
          className="hist-input"
          placeholder="Region…"
          value={params.region || ""}
          onChange={(e) => set({ region: e.target.value || undefined })}
        />
        <div className="hist-pills">
          {RISKS.map((r) => (
            <button
              key={r}
              className={"ev-tab" + (params.risk_level === r ? " active" : "")}
              onClick={() => set({ risk_level: params.risk_level === r ? undefined : r })}
            >
              {r}
            </button>
          ))}
        </div>
        <input
          type="date"
          className="hist-input"
          value={params.date_from?.slice(0, 10) || ""}
      onChange={(e) => set({ date_from: e.target.value ? e.target.value + "T00:00:00" : undefined })}
        />
        <input
          type="date"
          className="hist-input"
          value={params.date_to?.slice(0, 10) || ""}
          onChange={(e) => set({ date_to: e.target.value ? e.target.value + "T23:59:59" : undefined })}
        />
        <button className="hist-reset" onClick={() => setParams({ limit: PAGE, offset: 0 })}>Reset</button>
        <a className="hist-export" href="/api/v1/export/csv">Export CSV</a>
      </div>

      {err && <div className="hist-error">Failed to load: {err}</div>}

      <div className="hist-table">
        <div className="hist-row hist-head">
          <div>Time</div>
          <div>Region</div>
          <div>Score</div>
          <div>Prob</div>
          <div>Risk</div>
          <div>Budget</div>
          <div>Months</div>
        </div>
        {loading &&
          Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="hist-row hist-skel">
              <div /><div /><div /><div /><div /><div /><div />
            </div>
          ))}
        {!loading && data.items.length === 0 && (
          <div className="cr-empty">No evaluations match the current filters.</div>
        )}
        {!loading &&
          data.items.map((it) => (
            <button
              key={it.id}
              className="hist-row hist-item"
              onClick={() => nav("/evaluate?snapshot=" + it.id)}
            >
              <div className="tn">{fmt(it.created_at)}</div>
              <div>{it.region}</div>
              <div className="tn">{it.total_score.toFixed(1)}</div>
              <div className="tn">{(it.success_probability * 100).toFixed(0)}%</div>
              <div className={"hist-risk r-" + (it.risk_level || "").toLowerCase()}>{it.risk_level}</div>
              <div className="tn">${it.budget.toLocaleString()}</div>
              <div className="tn">{it.duration_months}</div>
            </button>
          ))}
      </div>

      <div className="hist-pager">
        <button
          disabled={page <= 1}
          onClick={() => setParams((p) => ({ ...p, offset: Math.max(0, (p.offset || 0) - PAGE) }))}
        >
          ← Prev
        </button>
        <span>Page {page} / {pages}</span>
        <button
          disabled={page >= pages}
          onClick={() => setParams((p) => ({ ...p, offset: (p.offset || 0) + PAGE }))}
        >
          Next →
        </button>
      </div>
    </div>
  );
}
