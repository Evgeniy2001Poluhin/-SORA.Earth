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
      // Population precedence (F076). A population the API sends replaces the
      // static RUSSIA_REGIONS value when both exist. A `score_kind: "mock"` row is
      // the hardcoded fallback, and its population is a placeholder rather than a
      // source, so the static value stays for it. The backend reads `population`
      // from the same russia_regions.ts file for database rows, so today the two
      // agree there; the rule changes a value only once the API carries its own.
      population: a.score_kind !== "mock" && Number.isFinite(a.population) && a.population > 0
        ? a.population
        : r.population,
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
      staleSince: a.stale_since ?? null,
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
    sourcesUsed: region.sourcesUsed,
    scoreKind: region.scoreKind,
    scoreVintage: region.scoreVintage,
    updatedAt: region.updatedAt,
    staleSince: region.staleSince,
    code: region.code,
  };
}
