# LSTM status: membership and observation eligibility

`GET /api/v1/lstm-status` reports two independent facts. Training eligibility
requires `observed_samples >= threshold`; it never guarantees that fitting succeeds
or that the resulting forecast is accurate. `observed_samples` uses the same helper
as EnsembleForecaster: sum of the loader's boolean `is_observed` mask. Frames
without provenance retain the existing row-count fallback.

Successful responses add required non-negative integer `observed_samples` and
`observations_remaining`, and boolean `eligible_for_training`. The deficit is a
count of observations, not days. No activation date is inferred by the widget.

`lstm_in_ensemble` is true only when the successful fitted ensemble includes LSTM,
false when the successful fit excludes it, and null when membership is unknown.
A fit failure returns `ensemble_fit_failed` with empty weights/models. No observed
data returns `no_observations` without attempting a fit. Query failure retains the
existing 503 unavailable contract. Missing membership on an older API is unknown.
A failed background refetch hides cached success in the widget.

For compatibility, `samples`, `active`, `days_remaining`, and
`next_activation_date` retain their historical dense-row semantics. Consumers
must migrate away from `active` as a fitted-membership signal and from the legacy
date as an activation forecast. The widget never falls back to those claims.

`sora_forecast_lstm_active` reports 1/0/NaN for present/absent/unknown. Each request
clears the old value before queries; startup also reports NaN; dashboards must treat NaN as unknown rather
than zero. The gauge reflects the last completed status request, not model quality
or an independently persisted training event.

The parent PR #475 fit cache and its locking remain unchanged. Failed fits are
cached until the series changes; a recovery TTL is a separate decision. Eligibility
and membership therefore describe this fitted-series outcome, not a guarantee
about every production inference request.

This focused change is stacked on #475 (`lstm-status-no-refit`). PR #466 also edits
the widget; its effective diff must be reconciled and rechecked during integration.
Tests use synthetic frames and fake member fits; no real research labels, model
artifacts, or production systems are used or modified.
