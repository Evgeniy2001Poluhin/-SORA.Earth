import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/** Old `active` counts dense rows, not fitted membership. Never use it as a
 * fallback for the explicit nullable membership field, including on old APIs. */
type LSTMStatus = {
  status: "ok";
  samples: number;
  threshold: number;
  observed_samples?: number;
  eligible_for_training?: boolean;
  observations_remaining?: number;
  lstm_in_ensemble?: boolean | null;
  models_active?: string[];
  weights?: Record<string, number>;
  reason_code?: string | null;
};

const count = (v: unknown): number | null =>
  typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null;
const share = (v: unknown): string =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1
    ? `${(v * 100).toFixed(0)}%` : "unknown";

export function LSTMProgressWidget() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["lstm-status"],
    queryFn: () => api<LSTMStatus>("/lstm-status"),
    refetchInterval: 30000,
  });
  if (isLoading) return (
    <div className="lstm-progress-widget loading">
      <div className="spinner" /><span>Loading LSTM status...</span>
    </div>
  );
  // Query retains old data after failed refetch: the error takes precedence.
  if (isError) return (
    <div className="lstm-progress-widget pending">
      <div className="widget-header"><h3>LSTM Training Progress</h3>
        <span className="badge pending">Unavailable</span></div>
      <p>Status could not be determined.</p>
    </div>
  );
  if (!data || count(data.samples) === null || count(data.threshold) === null || data.threshold <= 0) return null;

  const observed = count(data.observed_samples);
  const remaining = count(data.observations_remaining);
  const membership = typeof data.lstm_in_ensemble === "boolean" ? data.lstm_in_ensemble : null;
  const active = membership === true;
  const progress = observed === null ? null : observed / data.threshold * 100;
  const label = active ? "✓ Active" : membership === false ? "Not in ensemble" : "Unknown";

  return (
    <div className={`lstm-progress-widget ${active ? "ready" : "pending"}`}>
      <div className="widget-header"><h3>LSTM Training Progress</h3>
        <span className={`badge ${active ? "ready" : "pending"}`}>{label}</span></div>
      {progress === null ? <p>Observation count unknown; {data.samples} series rows.</p> : (
        <div className="progress-bar-container">
          <div className="progress-bar-fill" style={{ width: `${Math.min(progress, 100)}%`, backgroundColor: active ? "#2FE0A6" : "#F5C84B" }} />
          <span className="progress-label">{observed} / {data.threshold} observations ({progress.toFixed(1)}%)</span>
        </div>
      )}
      {data.eligible_for_training === false && remaining !== null && remaining > 0 && (
        <p>{remaining} more observations needed for training eligibility; no activation date is predicted.</p>
      )}
      {data.reason_code === "ensemble_fit_failed" && <p>Ensemble fit failed; LSTM membership is unknown.</p>}
      {active && <p>LSTM is active in ensemble for this fitted series.</p>}
      <div style={{ fontSize: 13 }}>
        <strong>Fitted models:</strong> {data.models_active?.join(", ") || "unknown"}<br />
        <strong>Weights:</strong> LSTM {share(data.weights?.lstm)}, Prophet {share(data.weights?.prophet)}
      </div>
      <div className="widget-footer"><span className="info-text">
        Training eligibility requires {data.threshold} observed points; it does not guarantee a successful fit or forecast accuracy.
      </span></div>
    </div>
  );
}
