import type { Region } from "@/hooks/useRussiaMap";
import { RUSSIA_REGIONS, type RussianRegion, type EnrichedRussianRegion } from "@/data/russia_regions";
import type { RegionEsgCardProps } from "./RegionEsgCard";

/**
 * Merge API regions onto the static region list.
 * Extracted from RussiaMapModal's useMemo so it can be tested without leaflet.
 * The modal cannot render in jsdom (react-leaflet), so the path from the API to
 * the card is tested through these two functions — keep the modal calling them
 * rather than rebuilding props inline.
 */
export function enrichRegions(
  apiRegions: Region[] | undefined,
  base: RussianRegion[] = RUSSIA_REGIONS
): EnrichedRussianRegion[] {
  if (!apiRegions?.length) return base;
  const apiMap = new Map(apiRegions.map(r => [r.code, r]));
  return base.map(r => {
    const a = apiMap.get(r.code);
    if (!a) return r;
    return {
      ...r,
      esgScore: a.esg?.score ?? r.esgScore,
      esgBreakdown: a.esg,
      confidence: a.confidence,
      sourcesUsed: a.sources_used,
      // These two fields were the ones this merge used to drop. On the live map
      // (2026-09-26, RU-YAN) the card never showed "Структурный индекс, данные
      // 2024", and would not have shown the "Заглушка" warning when the database
      // is empty. The card's own tests passed the kind directly, which is why it
      // went unseen.
      scoreKind: a.score_kind ?? null,
      scoreVintage: a.score_vintage ?? null,
      updatedAt: a.updated_at,
    };
  });
}

/**
 * Extract the props the modal passes to RegionEsgCard.
 * The modal cannot render in jsdom (react-leaflet), so the path from the API to
 * the card is tested through these two functions — keep the modal calling them
 * rather than rebuilding props inline.
 */
export function cardPropsOf(region: EnrichedRussianRegion): RegionEsgCardProps {
  return {
    score: Number(region.esgScore),
    breakdown: region.esgBreakdown,
    confidence: region.confidence,
    sourcesUsed: region.sourcesUsed,
    scoreKind: region.scoreKind,
    scoreVintage: region.scoreVintage,
    updatedAt: region.updatedAt,
  };
}
