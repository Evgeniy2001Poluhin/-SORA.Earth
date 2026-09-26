/**
 * The ESG block of a region's card.
 *
 * Extracted from `RussiaMapModal` so it can be asserted on directly: the map is
 * react-leaflet, and selecting a region in jsdom means clicking a layer rather
 * than an element, which left this block — the part that makes claims about a
 * number — untestable. `RussiaMapModal.tsx` had no tests at all.
 */
import { ScoreKindNote } from "./ProvenanceNotes";
import { sourcesCountText } from "./regionProvenance";

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

      <ScoreKindNote scoreKind={scoreKind} scoreVintage={scoreVintage} />

      <Row k="Total" v={Number(score).toFixed(1)} />
      {breakdown && (
        <>
          <Row k="E (environmental)" v={Number(breakdown.e_score).toFixed(1)} color="#5A9A6F" />
          <Row k="S (social)"        v={Number(breakdown.s_score).toFixed(1)} color="#8FB069" />
          <Row k="G (governance)"    v={Number(breakdown.g_score).toFixed(1)} color="#C9A96E" />
        </>
      )}

      {confidence != null && (
        <Row k="Источников" v={sourcesCountText(confidence)} />
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
