/**
 * Provenance rules for the global ESG map (F070-F073).
 *
 * `/api/v1/map/countries` tags each country with `esg_source`.
 * `world_bank_live` is the value the backend derived from World Bank data.
 * `hardcoded` is the static reference profile in app/api/map_data.py (COUNTRIES),
 * used when the backend has no data for the country.
 *
 * Two rules follow, and the page depends on both:
 *
 * 1. Renewables and CO2 per capita are shown as measured only when the source is
 *    the live one. For a static profile the backend fills both from its ESG score
 *    with formulas, so those numbers are not measurements. Anything not known to
 *    be live renders as "no data".
 *
 * 2. Leaders, top-N and averages are computed over one kind of source, and the
 *    page states which kind and how many countries it used. A static profile is
 *    not ranked against a live score as if they were the same kind of number.
 *
 * The backend's `world_bank_live` token is taken at face value. The backend also
 * emits it when the value came from BENCHMARKS, because `get_merged_country_data`
 * falls back to the static benchmark when the live cache has no valid entry. So
 * "live" here means what the backend labels live; this page cannot verify it.
 */

export interface MapCountry {
  code: string;
  name: string;
  lat: number;
  lon: number;
  esg: number;
  band: string;
  esg_source?: string | null;
  co2_intensity_t_per_capita?: number | null;
  renewable_share_pct?: number | null;
}

export const BAND_COLOR: Record<string, string> = {
  leader:     "#16a34a",
  advanced:   "#65a30d",
  developing: "#ca8a04",
  emerging:   "#ea580c",
  lagging:    "#dc2626",
};

export const LIVE_SOURCE = "world_bank_live";
export const STATIC_SOURCE = "hardcoded";

export type SourceKind = "live" | "static" | "unknown";

export function sourceKindOf(source: string | null | undefined): SourceKind {
  if (source === LIVE_SOURCE) return "live";
  if (source === STATIC_SOURCE) return "static";
  return "unknown";
}

/** The text the country tooltip shows after "source:". */
export function sourceLabelOf(source: string | null | undefined): string {
  switch (sourceKindOf(source)) {
    case "live":
      return LIVE_SOURCE;
    case "static":
      return "static reference value";
    default:
      return source ? source : "unknown";
  }
}

export interface MeasuredPhysical {
  renewable_share_pct: number | null;
  co2_intensity_t_per_capita: number | null;
}

const finiteOrNull = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

/** Renewables and CO2 per capita, only when they come from the live source.
 *  Everything else is null, which the page renders as "no data". */
export function measuredPhysicalOf(c: MapCountry): MeasuredPhysical {
  if (sourceKindOf(c.esg_source) !== "live") {
    return { renewable_share_pct: null, co2_intensity_t_per_capita: null };
  }
  return {
    renewable_share_pct: finiteOrNull(c.renewable_share_pct),
    co2_intensity_t_per_capita: finiteOrNull(c.co2_intensity_t_per_capita),
  };
}

export interface AggregateScope {
  /** The countries the leaders, top-N and average are computed over. */
  included: MapCountry[];
  /** Every country in the payload. */
  total: number;
  /** Names the kind of source used and the number of countries. */
  label: string;
}

/** Which countries the aggregates may use, and how to say so.
 *
 *  - Any country whose source is missing or unrecognised: the kinds cannot be
 *    told apart, so every country is used and the label says "mixed".
 *  - Otherwise, if any country is live: only the live ones are used.
 *  - Otherwise every country is static, and the label says so. */
export function aggregateScopeOf(countries: MapCountry[]): AggregateScope {
  const total = countries.length;
  if (total === 0) return { included: [], total, label: "no countries" };

  const kinds = countries.map((c) => sourceKindOf(c.esg_source));
  if (kinds.includes("unknown")) {
    return {
      included: countries,
      total,
      label: `mixed or unlabelled sources: N = ${total} countries`,
    };
  }

  const live = countries.filter((c) => sourceKindOf(c.esg_source) === "live");
  if (live.length > 0) {
    const n = live.length === total ? `${total} countries` : `${live.length} of ${total} countries`;
    return { included: live, total, label: `live source only (${LIVE_SOURCE}): N = ${n}` };
  }

  return {
    included: countries,
    total,
    label: `static reference values only: N = ${total} countries`,
  };
}

/** Highest ESG first. Returns a new array; the input is not reordered. */
export function byEsgDesc(countries: MapCountry[]): MapCountry[] {
  return [...countries].sort((a, b) => b.esg - a.esg);
}
