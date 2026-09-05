import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

/** GET /api/v1/lstm-status, migrated to a declared contract.
 *
 *  `active` is `null` when the check could not run: "we could not determine
 *  whether LSTM is active" and "LSTM is not active" are different answers, and
 *  the failure branch used to send the second for both. `days_remaining` is
 *  null for the same reason -- zero reads as "ready today".
 *
 *  That branch is now a 503, so it arrives as a rejection rather than as data. */
type LSTMStatus = {
  status: "ok";
  active: boolean;
  samples: number;
  threshold: number;
  days_remaining: number;
  next_activation_date: string | null;
  models_active: string[];
  weights: Record<string, number>;
  message: string;
};

export function LSTMProgressWidget() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["lstm-status"],
    queryFn: () => api<LSTMStatus>("/lstm-status"),
    refetchInterval: 30000, // Refresh every 30s
  });

  if (isLoading) {
    return (
      <div className="lstm-progress-widget loading">
        <div className="spinner" />
        <span>Loading LSTM status...</span>
      </div>
    );
  }

  // A 503 rejects, so `data` is absent -- the same state as "nothing loaded".
  // Rendering nothing would hide a fault; the widget says the status could not
  // be read instead of quietly disappearing.
  if (isError) {
    return (
      <div className="lstm-progress-widget pending">
        <div className="widget-header">
          <h3>LSTM Training Progress</h3>
          <span className="badge pending">Unavailable</span>
        </div>
        <p style={{ fontSize: 13, opacity: 0.9 }}>
          Status could not be determined.
        </p>
      </div>
    );
  }

  // Same rule (#236): `{}` passed this guard and `samples / threshold`
  // rendered "/ samples (NaN%)" -- a progress bar with no progress in it.
  // A zero threshold would divide to Infinity, so it is excluded here too.
  if (!data || !Number.isFinite(data.samples) || !(data.threshold > 0)) {
    return null;
  }

  const { active, samples, threshold, days_remaining, next_activation_date, models_active, weights } = data;
  const progress_pct = (samples / threshold) * 100;

  return (
    <div className={`lstm-progress-widget ${active ? "ready" : "pending"}`}>
      <div className="widget-header">
        <h3>LSTM Training Progress</h3>
        {active && <span className="badge ready">✓ Active</span>}
        {!active && <span className="badge pending">Collecting Data</span>}
      </div>

      <div className="progress-bar-container">
        <div
          className="progress-bar-fill"
          style={{
            width: `${Math.min(progress_pct, 100)}%`,
            backgroundColor: active ? "#2FE0A6" : "#F5C84B"
          }}
        />
        <span className="progress-label">
          {samples} / {threshold} samples ({progress_pct.toFixed(1)}%)
        </span>
      </div>

      {!active && days_remaining > 0 && (
        <div className="countdown">
          <span className="countdown-label">Estimated ready in:</span>
          <span className="countdown-value">
            {days_remaining === 0 ? "Today" : `${days_remaining} days`}
          </span>
          {next_activation_date && (
            <span className="countdown-date">({next_activation_date})</span>
          )}
        </div>
      )}

      {active && (
        <div className="ready-message">
          <p>✨ LSTM is active in ensemble with {samples} samples</p>
          <div style={{ marginTop: "8px", fontSize: "13px", opacity: 0.9 }}>
            {/* Guarded on the arrays themselves, not on `active` alone. The
                contract promises them, but reading a field on the strength of
                a sibling's value is the mistake #236 was about. */}
            <strong>Active models:</strong> {(models_active ?? []).join(", ") || "—"}
            <br />
            <strong>Weights:</strong> LSTM {((weights?.lstm ?? 0) * 100).toFixed(0)}%, Prophet {((weights?.prophet ?? 0) * 100).toFixed(0)}%
          </div>
        </div>
      )}

      <div className="widget-footer">
        <span className="info-text">
          LSTM requires {threshold}+ samples for accurate sequential forecasting
        </span>
      </div>
    </div>
  );
}
