/**
 * Provenance rules for the global ESG map (F070-F073).
 *
 * `/api/v1/map/countries` tags each country with `esg_source`, and sends
 * `esg_source_meanings` describing each token.
 * `world_bank_live`: a fresh live-cache entry.
 * `static_benchmark`: the BENCHMARKS row, when the live cache is empty or stale.
 * `hardcoded`: the COUNTRIES profile, when there is no external data.
 * The two static kinds are classified alike; see STATIC_SOURCES.
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
 * The `world_bank_live` token is taken at face value. The backend now sends it only
 * for a fresh live-cache entry, but an older backend also labelled benchmark-only
 * scores with it, and this page cannot tell the two apart.
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

/** A score from a fresh live-cache entry. */
export const LIVE_SOURCE = "world_bank_live";

/** Both static kinds: the score comes from a table in the source tree, not from
 *  live data. `static_benchmark` is the BENCHMARKS row used when the live cache is
 *  empty or stale; `hardcoded` is the COUNTRIES profile used with no external data.
 *  Before the backend split them, the first was labelled live. */
export const STATIC_SOURCES: ReadonlySet<string> = new Set(["hardcoded", "static_benchmark"]);

export type SourceKind = "live" | "static" | "unknown";

export function sourceKindOf(source: string | null | undefined): SourceKind {
  if (source === LIVE_SOURCE) return "live";
  if (source != null && STATIC_SOURCES.has(source)) return "static";
  return "unknown";
}

/** The text the country tooltip shows after "source:": the backend's meaning for
 *  the token when the response carries one, otherwise the raw token, or "unknown"
 *  when there is no token at all. */
export function sourceLabelOf(
  source: string | null | undefined,
  meanings?: Readonly<Record<string, string>> | null,
): string {
  if (source && meanings && Object.prototype.hasOwnProperty.call(meanings, source)) {
    const meaning = meanings[source];
    if (typeof meaning === "string" && meaning.trim() !== "") return meaning;
  }
  return source ? source : "unknown";
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
