/**
 * The ESG block of a region's card.
 *
 * Extracted from `RussiaMapModal` so it can be asserted on directly: the map is
 * react-leaflet, and selecting a region in jsdom means clicking a layer rather
 * than an element, which left this block — the part that makes claims about a
 * number — untestable. `RussiaMapModal.tsx` had no tests at all.
 */
export type RegionEsgBreakdown = {
  e_score?: number | null;
  s_score?: number | null;
  g_score?: number | null;
};

export type RegionEsgCardProps = {
  score: number;
  breakdown?: RegionEsgBreakdown | null;
  /** `len(distinct source names) / 3` in the aggregator — see below. */
  confidence?: number | null;
  sourcesUsed?: string[] | null;
  /** The aggregator's own `SCORE_KIND`, carried with the score. */
  scoreKind?: string | null;
  /** The reference period of the data behind it, from the rosstat snapshot. */
  scoreVintage?: string | null;
  /** When the score was recomputed -- not when its inputs were measured. */
  updatedAt?: string | null;
};

function Row({ k, v, color }: { k: string; v: string; color?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid #1a1d20" }}>
      <span style={{ opacity: 0.6 }}>{k}</span>
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {color && <span style={{ width: 8, height: 8, borderRadius: "50%", background: color }} />}
        {v}
      </span>
    </div>
  );
}

export default function RegionEsgCard({
  score, breakdown, confidence, sourcesUsed, scoreKind, scoreVintage, updatedAt,
}: RegionEsgCardProps) {
  return (
    <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #1a1d20" }}>
      <div style={{ fontSize: 11, opacity: 0.5, marginBottom: 2, textTransform: "uppercase", letterSpacing: 0.5 }}>
        ESG Score
      </div>

      {/* What the number is. The aggregator declares it beside the formula
          (`SCORE_KIND = "structural"`, with a measurement next to it: one
          distinct value per region against 99.9 for openmeteo temperature over
          the same window) and the rosstat ingester declares the vintage. Both
          travel with the score now; before this they lived only in the
          aggregator's own run result and reached no screen. */}
      {/* The map serves two hardcoded regions when both database readers come
          back empty. The API says so in `source: "mock-esg-v1-fallback"`; the
          hook took `j.regions` and dropped it, so Moscow showed 89.0 with
          nothing to mark it invented. This platform has already served invented
          ESG scores to production for weeks with every health check passing. */}
      {scoreKind === "mock" && (
        <div style={{ fontSize: 10, marginBottom: 6, lineHeight: 1.35, color: "#B85C5C" }}>
          Заглушка — данных по региону нет, число не измерено
        </div>
      )}

      {scoreKind === "structural" && (
        <div style={{ fontSize: 10, opacity: 0.45, marginBottom: 6, lineHeight: 1.35 }}>
          Структурный индекс{scoreVintage ? `, данные ${scoreVintage}` : ""}
          {" "}— сравнивает регионы между собой, не отражает изменения во времени
        </div>
      )}

      <Row k="Total" v={Number(score).toFixed(1)} />
      {breakdown && (
        <>
          <Row k="E (environmental)" v={Number(breakdown.e_score).toFixed(1)} color="#5A9A6F" />
          <Row k="S (social)"        v={Number(breakdown.s_score).toFixed(1)} color="#8FB069" />
          <Row k="G (governance)"    v={Number(breakdown.g_score).toFixed(1)} color="#C9A96E" />
        </>
      )}

      {/* `confidence` is `len(distinct source names) / 3`, and the required
          metric set spans exactly two sources — so it was 0.67 for all 85
          regions on every input. Measured over 27 value combinations: 26
          distinct total scores, one distinct confidence. Rendered as "67%" in
          green it read as "the model is two-thirds sure of this figure".
          Shown as the count it is, without a traffic light it cannot earn. */}
      {confidence != null && (
        <Row k="Источников" v={`${Math.round(confidence * 3)} из 3 ожидаемых`} />
      )}

      {sourcesUsed && sourcesUsed.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 11, opacity: 0.55 }}>
          Sources: {sourcesUsed.join(", ")}
        </div>
      )}

      {updatedAt && (
        <div style={{ marginTop: 4, fontSize: 10, opacity: 0.4 }}>
          Updated: {new Date(updatedAt).toLocaleString("ru-RU")}
        </div>
      )}
    </div>
  );
}
