/**
 * Shared provenance labeling for region ESG scores.
 *
 * Each screen used to format a region's provenance itself; #395 fixed the
 * card, but the tooltip kept "conf 67%" and the region page "Confidence 67%".
 * This file is now the one home for score-kind notes shown across all screens.
 */

type ScoreKindNoteProps = {
  scoreKind?: string | null;
  scoreVintage?: string | null;
};

export function ScoreKindNote({ scoreKind, scoreVintage }: ScoreKindNoteProps) {
  /* What the number is. The aggregator declares it beside the formula
     (`SCORE_KIND = "structural"`, with a measurement next to it: one
     distinct value per region against 99.9 for openmeteo temperature over
     the same window) and the rosstat ingester declares the vintage. Both
     travel with the score now; before this they lived only in the
     aggregator's own run result and reached no screen. */
  /* The map serves two hardcoded regions when both database readers come
     back empty. The API says so in `source: "mock-esg-v1-fallback"`; the
     hook took `j.regions` and dropped it, so Moscow showed 89.0 with
     nothing to mark it invented. This platform has already served invented
     ESG scores to production for weeks with every health check passing. */
  if (scoreKind === "mock") {
    return (
      <div style={{ fontSize: 10, marginBottom: 6, lineHeight: 1.35, color: "#B85C5C" }}>
        Заглушка — данных по региону нет, число не измерено
      </div>
    );
  }

  if (scoreKind === "structural") {
    return (
      <div style={{ fontSize: 10, opacity: 0.45, marginBottom: 6, lineHeight: 1.35 }}>
        Структурный индекс{scoreVintage ? `, данные ${scoreVintage}` : ""}
        {" "}— сравнивает регионы между собой, не отражает изменения во времени
      </div>
    );
  }

  return null;
}
