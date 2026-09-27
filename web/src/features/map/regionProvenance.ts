/**
 * Shared provenance wording for region ESG scores.
 *
 * Each screen used to format a region's provenance itself; #395 fixed the
 * card, but the tooltip kept "conf 67%" and the region page "Confidence 67%".
 * This file is now the one home for that wording.
 */

/** `confidence` is `len(distinct source names) / 3`, and the required
    metric set spans exactly two sources — so it was 0.67 for all 85
    regions on every input. Measured over 27 value combinations: 26
    distinct total scores, one distinct confidence. Rendered as "67%" in
    green it read as "the model is two-thirds sure of this figure".
    Shown as the count it is, without a traffic light it cannot earn. */
export function sourcesCountText(confidence: number): string {
  return `${Math.round(confidence * 3)} из 3 ожидаемых`;
}

/** Short-form stale warning for tooltips: day and month only. */
export function staleShortText(staleSince: string | null | undefined): string | null {
  if (!staleSince) return null;
  const date = new Date(staleSince);
  const formatted = date.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
  return `не подтверждено с ${formatted}`;
}
