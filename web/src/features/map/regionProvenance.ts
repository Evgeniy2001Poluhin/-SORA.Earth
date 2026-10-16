/**
 * Shared provenance wording for region ESG scores.
 *
 * Each screen used to format a region's provenance itself; #395 fixed the
 * card, but the tooltip kept "conf 67%" and the region page "Confidence 67%".
 * This file is now the one home for that wording.
 */

/** How many sources the score stands on, counted from the names the API lists.
 *
 *  There is no denominator here. The map's expected set is REQUIRED_METRICS in
 *  app/services/esg_aggregator.py, which spans two sources, but the API does not
 *  send it, so "N из 3 ожидаемых" had a constant for its denominator (F074).
 *  `confidence` is not used either: the backend computes it as min(1, n/3), so
 *  it stops being a count once n reaches 3.
 *
 *  Returns null when the API lists no sources. An empty list is shown as no
 *  count, not as one inferred from `confidence`. */
export function sourcesUsedCount(sourcesUsed: readonly string[] | null | undefined): number | null {
  return sourcesUsed && sourcesUsed.length > 0 ? sourcesUsed.length : null;
}

/** The detail page's source count, "N из M ожидаемых", where M is the expected
 *  set. The detail response's `inputs` carries one row per required metric
 *  (REQUIRED_METRICS in the backend), so its distinct sources are the explicit
 *  expected set. A source counts as present when one of its metrics has a value.
 *
 *  Without inputs (the read failed) it falls back to the sources the API lists,
 *  with no denominator. Returns null when neither is available. */
export function detailSourcesText(
  inputs: ReadonlyArray<{ source: string; missing: boolean }> | null | undefined,
  sourcesUsed: readonly string[] | null | undefined,
): string | null {
  if (inputs && inputs.length > 0) {
    const expected = new Set(inputs.map((i) => i.source));
    const present = new Set(inputs.filter((i) => !i.missing).map((i) => i.source));
    return `${present.size} из ${expected.size} ожидаемых`;
  }
  const count = sourcesUsedCount(sourcesUsed);
  return count == null ? null : String(count);
}

/** Short-form stale warning for tooltips: day and month only. */
export function staleShortText(staleSince: string | null | undefined): string | null {
  if (!staleSince) return null;
  const date = new Date(staleSince);
  const formatted = date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
  return `не подтверждено с ${formatted}`;
}
