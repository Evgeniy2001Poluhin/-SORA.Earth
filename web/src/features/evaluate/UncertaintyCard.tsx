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

  // Absent and empty are different answers, and only one of them is a
  // reason to draw an interval (#236). `!q.data` alone guards `undefined`;
  // a 200 carrying `{}` is truthy, and every read below is `?? 0`, so the
  // card used to paint a complete confidence interval reading 0.0% — a
  // number the model never produced, on a page that looks normal. Measured,
  // not deduced: see UncertaintyCard.production.test.tsx.
  //
  // Guarded on the fields actually rendered rather than on the object, so a
  // payload that arrives without them renders nothing, exactly as a failed
  // request already does. All three checks use `typeof !== "number"` rather
  // than truthiness: a mean of 0 is a real answer (every tree votes "no"),
  // and 0 is falsy.
  if (typeof q.data?.prediction?.mean !== "number" ||
      typeof q.data?.tree_distribution?.n_trees !== "number" ||
      typeof q.data?.tree_distribution?.votes_for !== "number") return null;
  const u = q.data;
  const meanProb = (u.prediction.mean * 100).toFixed(1);

  return (
    <div className="uncertainty-card">
      <div className="uncertainty-head">
        <div className="eyebrow">Tree vote (RandomForest)</div>
        <span className="uc-badge" style={{ background: (CONF_COLOR[u.confidence] ?? "#888") + "22", color: CONF_COLOR[u.confidence] ?? "#888", borderColor: CONF_COLOR[u.confidence] ?? "#888" }}>
          {(u.confidence ?? "").toUpperCase()} CONFIDENCE
        </span>
      </div>

      <div style={{ padding: "16px 0", fontSize: "14px" }}>
        <div style={{ fontSize: "28px", fontWeight: "600", color: "var(--text)", marginBottom: "8px" }}>
          {meanProb}%
        </div>
        <div style={{ color: "var(--muted)" }}>
          {u.tree_distribution.votes_for} of {u.tree_distribution.n_trees} trees vote for success
        </div>
      </div>
    </div>
  );
}
