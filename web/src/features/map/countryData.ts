/**
 * Rules for the global ESG map (Council decision C8).
 *
 * `/api/v1/map/countries` gives each country one score, or none:
 *
 * - `esg_status` "ok": all five inputs are in the pinned official table, each
 *   within its maximum age at the date of use. The score is shown.
 * - `esg_status` "incomplete": an input is missing, out of scale, too old or
 *   unsourced. `esg` is null, and `missing_inputs` names what is absent.
 *
 * Rules the page depends on:
 *
 * 1. A null score is never 0. It is drawn neutral, labelled incomplete, and
 *    left out of every ranking, leader count, top-N and average.
 * 2. Averages and leaders are over scored countries only, and the page states
 *    N as "N = x of y countries".
 * 3. Renewables and CO2 per capita are the pinned values, or null when the
 *    input does not qualify.
 * 4. An API error is shown as "unavailable", which is not the same as incomplete.
 */

/** One input as the backend pins it: its value, year, age and where it came from. */
export interface InputProvenance {
  value: number;
  unit: string | null;
  year: string;
  age_years: number;
  source: string;
  release: string | null;
  fetched_at: string | null;
}

export interface MapCountry {
  code: string;
  name: string;
  lat: number;
  lon: number;
  /** The score, or null when the country is incomplete. Never 0 for "no score". */
  esg: number | null;
  band: string | null;
  esg_status?: string | null;
  esg_source?: string | null;
  missing_inputs?: string[] | null;
  /** Each qualifying input, keyed by input name. */
  inputs?: Partial<Record<string, InputProvenance>> | null;
  input_notes?: Record<string, string> | null;
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

/** Drawn for a country with no score: not a band, and not a 0. */
export const NEUTRAL_COLOR = "#9ca3af";

/** The two statuses the backend sends. */
export type ScoreStatus = "ok" | "incomplete";

/** Human names for the inputs, so the page does not print field names. */
export const INPUT_LABELS: Record<string, string> = {
  renewable_share: "renewables share",
  co2_per_capita: "CO2 per capita",
  gini_index: "Gini index",
  gov_effectiveness: "government effectiveness",
  hdi: "human development index",
};

/** The score as a number, or null when there is none. Zero is a real score. */
export function scoreOf(c: MapCountry): number | null {
  return typeof c.esg === "number" && Number.isFinite(c.esg) ? c.esg : null;
}

export type ScoreKind = ScoreStatus | "unlabelled";

/** ok, incomplete, or unlabelled (a score with no status, from an older backend).
 *  A null score is always incomplete, whatever its status field says. */
export function statusOf(c: MapCountry): ScoreKind {
  if (scoreOf(c) === null) return "incomplete";
  if (c.esg_status === "ok") return "ok";
  return "unlabelled";
}

/** The order inputs are listed in, and the decimals each is shown with. */
export const INPUT_ORDER: readonly string[] = [
  "renewable_share", "co2_per_capita", "gini_index", "gov_effectiveness", "hdi",
];
const DECIMALS: Record<string, number> = {
  renewable_share: 1, co2_per_capita: 1, gini_index: 1, gov_effectiveness: 2, hdi: 3,
};

/** One scored input as a tooltip line: its value and unit, its year, and its source. */
export function describeInput(key: string, p: InputProvenance): { label: string; text: string } {
  const decimals = DECIMALS[key] ?? 2;
  const value = Number.isFinite(p.value) ? p.value.toFixed(decimals) : "no value";
  const unit = p.unit ? ` ${p.unit}` : "";
  return {
    label: INPUT_LABELS[key] ?? key,
    text: `${value}${unit}, ${p.year}, ${p.source}`,
  };
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

/** Renewables and CO2 per capita as the backend measured them. Anything not a
 *  finite number is "no data". */
export function measuredPhysicalOf(c: MapCountry): MeasuredPhysical {
  return {
    renewable_share_pct: finiteOrNull(c.renewable_share_pct),
    co2_intensity_t_per_capita: finiteOrNull(c.co2_intensity_t_per_capita),
  };
}

export interface AggregateScope {
  /** The scored countries: the only ones leaders, top-N and averages may use. */
  included: MapCountry[];
  /** Every country in the payload. */
  total: number;
  /** Countries with no score, excluded from everything above. */
  incomplete: number;
  /** Names N, and the statuses it counts. */
  label: string;
}

/** Which countries the aggregates may use, and how to say so.
 *
 *  Incomplete countries are always excluded. The label gives N as "N = x of y
 *  countries", with the ok count, so the reader can see what the average is over. */
export function aggregateScopeOf(countries: MapCountry[]): AggregateScope {
  const total = countries.length;
  const included = countries.filter((c) => scoreOf(c) !== null);
  const incomplete = total - included.length;

  if (total === 0) return { included, total, incomplete: 0, label: "no countries" };
  if (included.length === 0) {
    return { included, total, incomplete, label: `no complete score: N = 0 of ${total} countries` };
  }

  const count = (kind: ScoreKind) => included.filter((c) => statusOf(c) === kind).length;
  const split = [`${count("ok")} ok`];
  const unlabelled = count("unlabelled");
  if (unlabelled > 0) split.push(`${unlabelled} unlabelled`);
  const excluded = incomplete > 0 ? `; ${incomplete} incomplete, excluded` : "";

  return {
    included,
    total,
    incomplete,
    label: `N = ${included.length} of ${total} countries (${split.join(", ")})${excluded}`,
  };
}

/** The mean of the scores. Null countries are not averaged as 0: they are left out.
 *  Null when no country has a score, so the page shows no average rather than NaN. */
export function meanScore(countries: MapCountry[]): number | null {
  const scores = countries
    .map(scoreOf)
    .filter((s): s is number => s !== null);
  if (scores.length === 0) return null;
  return Math.round(scores.reduce((sum, s) => sum + s, 0) / scores.length);
}

/** Scored countries, highest first. Incomplete countries are not ranked.
 *  Returns a new array; the input is not reordered. */
export function byEsgDesc(countries: MapCountry[]): MapCountry[] {
  const scored = countries.filter((c): c is MapCountry & { esg: number } => scoreOf(c) !== null);
  return scored.sort((a, b) => b.esg - a.esg);
}
