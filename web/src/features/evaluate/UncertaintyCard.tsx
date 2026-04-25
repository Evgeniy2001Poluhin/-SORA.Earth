import { useQuery } from "@tanstack/react-query";
import { calibrationApi } from "@/api/endpoints/calibration";
import "./uncertainty.css";

interface Props {
  payload: {
    budget_usd?: number;
    co2_reduction_tons_per_year?: number;
    social_impact_score?: number;
    project_duration_months?: number;
  };
}

const CONF_COLOR: Record<string, string> = {
  high:   "#2FE0A6",
  medium: "#F5C84B",
  low:    "#EF4444",
};

export function UncertaintyCard({ payload }: Props) {
  const body = {
    budget: Number(payload.budget_usd ?? 0),
    co2_reduction: Number(payload.co2_reduction_tons_per_year ?? 0),
    social_impact: Number(payload.social_impact_score ?? 0),
    duration_months: Number(payload.project_duration_months ?? 1),
  };

  const q = useQuery({
    queryKey: ["uncertainty", body],
    queryFn: () => calibrationApi.uncertainty(body),
    enabled: body.budget > 0,
  });

  if (!q.data) return null;
  const u = q.data;
  const lo = u.prediction.lower_90 * 100;
  const med = u.prediction.median * 100;
  const hi = u.prediction.upper_90 * 100;
  const width = Math.max(1, hi - lo);
  const medPos = ((med - lo) / width) * 100;

  return (
    <div className="uncertainty-card">
      <div className="uncertainty-head">
        <div className="eyebrow">Confidence interval (tree-level)</div>
        <span className="uc-badge" style={{ background: (CONF_COLOR[u.confidence] ?? "#888") + "22", color: CONF_COLOR[u.confidence] ?? "#888", borderColor: CONF_COLOR[u.confidence] ?? "#888" }}>
          {u.confidence.toUpperCase()} CONFIDENCE
        </span>
      </div>

      <div className="uc-bar-wrap">
        <div className="uc-bar">
          <div className="uc-bar-fill" style={{ width: "100%" }} />
          <div className="uc-bar-marker" style={{ left: medPos + "%" }} />
        </div>
        <div className="uc-labels tabular">
          <span>{lo.toFixed(1)}%</span>
          <span style={{ color: "#2FE0A6" }}>median {med.toFixed(1)}%</span>
          <span>{hi.toFixed(1)}%</span>
        </div>
      </div>

      <div className="uc-meta">
        <div><span className="muted">std</span> <span className="tabular">{(u.tree_distribution.std * 100).toFixed(2)}%</span></div>
        <div><span className="muted">range</span> <span className="tabular">{(u.tree_distribution.min * 100).toFixed(1)}-{(u.tree_distribution.max * 100).toFixed(1)}%</span></div>
        <div><span className="muted">trees</span> <span className="tabular">{u.tree_distribution.n_trees}</span></div>
      </div>
    </div>
  );
}
