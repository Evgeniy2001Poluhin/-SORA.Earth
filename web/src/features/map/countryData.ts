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
 * 5. A score is admissible while the UTC date is before its `earliest_expiry`
 *    (that date is the first day the input is too old, so the score stops at
 *    00:00:00.000Z on it). The page withholds a score once the clock reaches
 *    that instant, without waiting for the server to say so.
 */

/** One input as the backend pins it: its value, year, age and where it came from. */
export interface InputProvenance {
  value: number;
  unit: string | null;
  year: string;
  age_years: number;
  /** The most the input may be old, in years, at the date the score was computed. */
  max_age_years?: number;
  /** The first day this value is too old (ISO date). */
  expires_at?: string;
  source: string;
  release: string | null;
  fetched_at: string | null;
}

/** Why a required input is missing. `code` is what the rules branch on; `text` is the sentence. */
export interface InputReason {
  code: string;
  text?: string;
  year?: string;
  age_years?: number;
  max_age_years?: number;
  expires_at?: string;
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
  /** Why each missing input is missing, keyed by input name. */
  input_reasons?: Partial<Record<string, InputReason>> | null;
  snapshot_date?: string | null;
  earliest_expiry?: string | null;
  co2_intensity_t_per_capita?: number | null;
  renewable_share_pct?: number | null;
  /** Set by `withClientExpiry`: the score is withheld because an input expired (UTC) before the page's clock. */
  client_expired?: boolean;
  /** The ISO date that withholds it (the country's `earliest_expiry`). */
  client_expired_on?: string | null;
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

/** One scored input as a tooltip line: its value and unit, its year, its source, and when it expires. */
export function describeInput(key: string, p: InputProvenance): { label: string; text: string } {
  const decimals = DECIMALS[key] ?? 2;
  const value = Number.isFinite(p.value) ? p.value.toFixed(decimals) : "no value";
  const unit = p.unit ? ` ${p.unit}` : "";
  const expiry = p.expires_at ? `, usable until ${p.expires_at}` : "";
  return {
    label: INPUT_LABELS[key] ?? key,
    text: `${value}${unit}, ${p.year}, ${p.source}${expiry}`,
  };
}

/** Why one input is missing, as a sentence a reader can act on. Distinguishes "no observation"
 *  from "observation too old", which need different fixes: the first is a gap in the source,
 *  the second a snapshot that must be refreshed. */
export function reasonSentence(key: string, reason: InputReason): string {
  const label = INPUT_LABELS[key] ?? key;
  if (reason.code === "no_observation") {
    return `${label}: no observation in the pinned source`;
  }
  if (reason.code === "too_old" && reason.year !== undefined) {
    return `${label}: observation too old (${reason.year}, ${reason.age_years} years old; `
      + `the limit is ${reason.max_age_years} years)`;
  }
  return `${label}: ${reason.text ?? reason.code}`;
}

const DAY_MS = 86_400_000;

/** The notice window when the payload does not say (`expiry_notice_days`). */
const DEFAULT_EXPIRY_NOTICE_DAYS = 60;

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** 00:00:00.000Z on an ISO calendar date (YYYY-MM-DD), in ms. Null for anything else,
 *  including a date that does not exist such as 2027-02-30. */
export function utcDayStartMs(isoDate: string | null | undefined): number | null {
  if (typeof isoDate !== "string") return null;
  const m = ISO_DATE.exec(isoDate);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  // setUTCFullYear, not Date.UTC: Date.UTC maps years 0-99 onto 1900-1999. The check below refuses a
  // date that rolls over, such as 2027-02-30 becoming March.
  const d = new Date(0);
  d.setUTCFullYear(year, month - 1, day);
  if (d.getUTCFullYear() !== year || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) return null;
  return d.getTime();
}

/** 00:00:00.000Z of the UTC calendar day that `nowMs` falls on. */
export function utcDayStartOf(nowMs: number): number {
  return Math.floor(nowMs / DAY_MS) * DAY_MS;
}

/** The next UTC midnight strictly after `nowMs`. */
export function nextUtcMidnightAfter(nowMs: number): number {
  return utcDayStartOf(nowMs) + DAY_MS;
}

/** Whole days from the UTC calendar date of `nowMs` to `isoDate`, counted as the backend counts
 *  them (`(expiry - today_utc).days`). Negative once the date has passed. Null when there is no
 *  date to count to, or no clock. */
export function utcDaysUntil(isoDate: string | null | undefined, nowMs: number): number | null {
  const target = utcDayStartMs(isoDate);
  if (target === null || !Number.isFinite(nowMs)) return null;
  return Math.round((target - utcDayStartOf(nowMs)) / DAY_MS);
}

/** True when the country has a score and `nowMs` has reached its expiry: 00:00:00.000Z on
 *  `earliest_expiry`, the first day the input is too old. A country with no score is never expired. */
export function isScoreExpiredAt(c: MapCountry, nowMs: number): boolean {
  if (scoreOf(c) === null) return false;
  const deadline = utcDayStartMs(c.earliest_expiry);
  return deadline !== null && nowMs >= deadline;
}

/** The countries as the page may show them at `nowMs`. A score whose expiry has passed is
 *  withheld: it becomes unscored, so it is drawn neutral and left out of every ranking and average,
 *  and it carries a marker naming the date. Every other country is returned as it was. */
export function withClientExpiry(countries: MapCountry[], nowMs: number): MapCountry[] {
  return countries.map((c) => {
    if (!isScoreExpiredAt(c, nowMs)) return c;
    return {
      ...c,
      esg: null,
      band: null,
      esg_status: "incomplete",
      client_expired: true,
      client_expired_on: c.earliest_expiry ?? null,
    };
  });
}

/** The ISO dates the held data stops being admissible on: the table's earliest expiry, and each
 *  scored country's `earliest_expiry`. An incomplete country has no score to lose, so adds nothing. */
export function expiryDatesOf(countries: readonly MapCountry[], tableEarliest: string | null | undefined): string[] {
  const dates = new Set<string>();
  const add = (iso: string | null | undefined) => {
    if (iso && utcDayStartMs(iso) !== null) dates.add(iso);
  };
  add(tableEarliest);
  for (const c of countries) {
    if (scoreOf(c) !== null) add(c.earliest_expiry);
  }
  return [...dates];
}

/** The earliest of `dates` that the clock has reached while the data was fetched before it, so the
 *  held data predates an expiry it cannot know about. Null when there is none. */
export function expiredSinceFetch(dates: readonly string[], nowMs: number, fetchedAtMs: number): string | null {
  let found: string | null = null;
  let foundMs = Number.POSITIVE_INFINITY;
  for (const iso of dates) {
    const ms = utcDayStartMs(iso);
    if (ms === null || ms > nowMs || ms <= fetchedAtMs) continue;
    if (ms < foundMs) {
      found = iso;
      foundMs = ms;
    }
  }
  return found;
}

/** The notice for the map: when the pinned data expires, counted from `nowMs` in UTC days. Null when
 *  the date is further off than `noticeDays` (default 60), or there is no date. `days` is negative
 *  once the expiry has passed. */
export function expiryNoticeText(earliest: string | null | undefined, nowMs: number,
  noticeDays?: number | null): string | null {
  const days = utcDaysUntil(earliest, nowMs);
  if (days === null || days > (noticeDays ?? DEFAULT_EXPIRY_NOTICE_DAYS)) return null;
  if (days < 0) {
    return `The pinned data expired on ${earliest}. Every country that needs an expired input is incomplete. `
      + "A newer snapshot must be pinned before scores return.";
  }
  const when = days === 0 ? "today" : days === 1 ? "in 1 day" : `in ${days} days`;
  return `The pinned data expires on ${earliest}, ${when}. Scores that need an expired input go incomplete then. `
    + "A newer snapshot must be pinned before that date.";
}

/** Shown while a refresh is in flight after an expiry the held data predates. */
export function expiredRefreshingText(expiredOn: string): string {
  return `The pinned data expired on ${expiredOn} (UTC) and is being refreshed. `
    + "Scores that needed it are withheld until then.";
}

/** Shown when the refresh after an expiry failed: the page has no fresh data it can trust. */
export function expiredUnavailableText(expiredOn: string): string {
  return `ESG map unavailable: the scores expired on ${expiredOn} (UTC) and fresh data could not be loaded.`;
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
