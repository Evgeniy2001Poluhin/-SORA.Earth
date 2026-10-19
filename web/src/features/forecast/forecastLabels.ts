/**
 * What the forecast responses support saying, shared by both forecast pages so
 * that the two cannot drift apart again.
 *
 * `/forecast` returns `history` from `load_time_series`
 * (app/services/forecasting/data_loader.py): one row per calendar day from the
 * first evaluation to the last, with the days between evaluations interpolated.
 * The response does not mark which days were measured, so a row count is a
 * count of days, not of observations.
 */

/**
 * The nominal level the server states for a band: `metadata.interval_level`.
 * `null` means the band mixes levels (the ensemble averages bounds); `undefined`
 * means an older response that does not carry the field.
 */
export type IntervalLevel = number | null | undefined;

const COVERAGE_CAVEAT = "model-based, coverage not validated";

/**
 * What a band may be called. The level is named only when the server states
 * it; otherwise the band is a neutral model interval. The coverage caveat is
 * kept in every case: no response carries a coverage figure.
 */
export function bandLabel(level: IntervalLevel): string {
  if (level === 0.9) return `nominal 90% interval (${COVERAGE_CAVEAT})`;
  if (level === 0.95) return `nominal 95% interval (${COVERAGE_CAVEAT})`;
  return `model interval (${COVERAGE_CAVEAT})`;
}

/**
 * The reference line sits on the last history date, which is the last
 * observation. It is not the current clock.
 */
export function lastObservationLabel(ds: string): string {
  return `last observation ${ds}`;
}

/**
 * The history's calendar span, from the dates in the data. Not an observation
 * count: the server does not send one.
 */
export function historySpan(history: ReadonlyArray<{ ds: string }>): string {
  const n = history.length;
  if (n === 0) return "no history";
  if (n === 1) return `1 calendar day, ${history[0].ds}`;
  return `${n} calendar days, ${history[0].ds} → ${history[n - 1].ds}; days without an evaluation are interpolated`;
}
