/**
 * The Tooltip's inner content for a region marker on RussiaMap.
 *
 * Extracted so it can be tested without the map — react-leaflet's `Tooltip`
 * renders as a layer, not an element, and clicking a region means finding and
 * clicking that layer, which left the tooltip's content claims untestable.
 * RussiaMap.tsx had no tests at all.
 */
import type { EnrichedRussianRegion } from "@/data/russia_regions";

interface RegionTooltipBodyProps {
  region: EnrichedRussianRegion;
}

export default function RegionTooltipBody({ region: r }: RegionTooltipBodyProps) {
  return (
    <>
      <strong>{r.capital}</strong>
      <div style={{ fontSize: 11, opacity: 0.8 }}>{r.name}</div>
      <div style={{ fontSize: 10, opacity: 0.6 }}>
        {r.district} · {(r.population / 1e6).toFixed(2)}M
        {r.esgScore != null && ` · ESG ${r.esgScore}`}
      </div>
      {r.esgBreakdown && (
        <div style={{ fontSize: 10, opacity: 0.75, marginTop: 2, display: "flex", gap: 6 }}>
          <span style={{ color: "#5A9A6F" }}>E{Math.round(r.esgBreakdown.e_score)}</span>
          <span style={{ color: "#8FB069" }}>S{Math.round(r.esgBreakdown.s_score)}</span>
          <span style={{ color: "#C9A96E" }}>G{Math.round(r.esgBreakdown.g_score)}</span>
        </div>
      )}
    </>
  );
}
