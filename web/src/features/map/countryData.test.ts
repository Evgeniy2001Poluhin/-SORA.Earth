/**
 * The rules the map's numbers stand on (C8). The page tests exercise these
 * through the rendered page; these pin the rules themselves, including the cases
 * the page fixtures do not reach.
 */
import { describe, expect, it } from "vitest";

import {
  aggregateScopeOf,
  byEsgDesc,
  describeInput,
  expiredSinceFetch,
  expiryDatesOf,
  isScoreExpiredAt,
  measuredPhysicalOf,
  meanScore,
  nextUtcMidnightAfter,
  scoreOf,
  sourceLabelOf,
  statusOf,
  utcDayStartMs,
  utcDaysUntil,
  withClientExpiry,
  type MapCountry,
} from "./countryData";

const country = (over: Partial<MapCountry>): MapCountry => ({
  code: "XX", name: "X", lat: 0, lon: 0, esg: 50, band: "emerging", ...over,
});

const OK = (name: string, esg: number, band = "developing") =>
  country({ name, esg, band, esg_status: "ok", esg_source: "pinned_table" });
const INCOMPLETE = (name: string, missing: string[] = ["gov_effectiveness"]) =>
  country({ name, esg: null, band: null, esg_status: "incomplete", esg_source: null, missing_inputs: missing });

describe("scoreOf", () => {
  it("reads a null score as no score, and a real 0 as a score", () => {
    expect(scoreOf(INCOMPLETE("Iceland"))).toBeNull();
    expect(scoreOf(OK("Zero", 0, "lagging"))).toBe(0);
  });

  it("refuses a non-finite number as a score", () => {
    expect(scoreOf(country({ esg: Number.NaN }))).toBeNull();
    expect(scoreOf(country({ esg: Number.POSITIVE_INFINITY }))).toBeNull();
  });
});

describe("statusOf", () => {
  it("is incomplete for every null score, whatever the status field says", () => {
    expect(statusOf(INCOMPLETE("Iceland"))).toBe("incomplete");
    expect(statusOf(country({ esg: null, esg_status: "ok" }))).toBe("incomplete");
  });

  it("reports ok as sent, and a score with no status as unlabelled", () => {
    expect(statusOf(OK("Sweden", 81.5))).toBe("ok");
    expect(statusOf(country({ name: "Old", esg: 60 }))).toBe("unlabelled");
  });

  it("does not know a mixed status: the backend no longer sends one", () => {
    expect(statusOf(country({ name: "Legacy", esg: 60, esg_status: "mixed" }))).toBe("unlabelled");
  });
});

describe("sourceLabelOf", () => {
  const MEANINGS = {
    pinned_table: "Every input is read from the pinned official table.",
  };

  it("shows the backend's meaning for the token when it sends one", () => {
    expect(sourceLabelOf("pinned_table", MEANINGS)).toBe("Every input is read from the pinned official table.");
  });

  it("falls back to the raw token when there is no meaning for it", () => {
    expect(sourceLabelOf("pinned_table")).toBe("pinned_table");
    expect(sourceLabelOf("mock", MEANINGS)).toBe("mock");
  });

  it("does not read an inherited property as a meaning", () => {
    expect(sourceLabelOf("constructor", MEANINGS)).toBe("constructor");
  });

  it("says unknown when there is no token", () => {
    expect(sourceLabelOf(undefined, MEANINGS)).toBe("unknown");
    expect(sourceLabelOf(null)).toBe("unknown");
  });
});

describe("measuredPhysicalOf", () => {
  it("passes through a finite measured value", () => {
    const v = measuredPhysicalOf(country({ renewable_share_pct: 60.1, co2_intensity_t_per_capita: 3.6 }));
    expect(v).toEqual({ renewable_share_pct: 60.1, co2_intensity_t_per_capita: 3.6 });
  });

  it("reports null for a missing or non-finite value, not NaN", () => {
    const v = measuredPhysicalOf(
      country({ renewable_share_pct: Number.NaN, co2_intensity_t_per_capita: undefined }),
    );
    expect(v).toEqual({ renewable_share_pct: null, co2_intensity_t_per_capita: null });
  });

  it("keeps a measured zero", () => {
    expect(measuredPhysicalOf(country({ renewable_share_pct: 0 })).renewable_share_pct).toBe(0);
  });
});

describe("aggregateScopeOf", () => {
  it("has no aggregate over an empty list", () => {
    const s = aggregateScopeOf([]);
    expect(s.included).toEqual([]);
    expect(s.label).toBe("no countries");
  });

  it("excludes incomplete countries and states N as x of y", () => {
    const s = aggregateScopeOf([INCOMPLETE("Iceland"), OK("Sweden", 81.5), OK("Japan", 77)]);

    expect(s.included.map((c) => c.name)).toEqual(["Sweden", "Japan"]);
    expect(s.total).toBe(3);
    expect(s.incomplete).toBe(1);
    expect(s.label).toBe("N = 2 of 3 countries (2 ok); 1 incomplete, excluded");
  });

  it("says N with no exclusion note when every country is scored", () => {
    const s = aggregateScopeOf([OK("Sweden", 81.5), OK("Chile", 62.5)]);
    expect(s.label).toBe("N = 2 of 2 countries (2 ok)");
  });

  it("says no complete score when every country is incomplete", () => {
    const s = aggregateScopeOf([INCOMPLETE("Iceland"), INCOMPLETE("Japan")]);
    expect(s.included).toEqual([]);
    expect(s.label).toBe("no complete score: N = 0 of 2 countries");
  });

  it("counts a score with no status as unlabelled, not as ok", () => {
    const s = aggregateScopeOf([country({ name: "Old", esg: 60 }), OK("Sweden", 81.5)]);
    expect(s.label).toBe("N = 2 of 2 countries (1 ok, 1 unlabelled)");
  });
});

describe("describeInput", () => {
  it("shows the value at the input's own precision, with its unit, year and source", () => {
    const line = describeInput("hdi", {
      value: 0.94, unit: "index, 0-1", year: "2023", age_years: 3,
      source: "UNDP Human Development Report", release: "release 2025", fetched_at: null,
    });

    expect(line.label).toBe("human development index");
    expect(line.text).toBe("0.940 index, 0-1, 2023, UNDP Human Development Report");
  });

  it("keeps a measured zero as 0", () => {
    const line = describeInput("renewable_share", {
      value: 0, unit: "%", year: "2021", age_years: 5, source: "World Bank", release: null, fetched_at: null,
    });
    expect(line.text).toBe("0.0 %, 2021, World Bank");
  });
});

describe("meanScore", () => {
  it("averages the scores", () => {
    expect(meanScore([OK("Sweden", 81.5), OK("Chile", 62.5)])).toBe(72);
  });

  it("does not average an incomplete country as 0", () => {
    expect(meanScore([OK("Sweden", 80), INCOMPLETE("Iceland")])).toBe(80);
  });

  it("counts a real 0 score in the average", () => {
    expect(meanScore([OK("Zero", 0, "lagging"), OK("Sweden", 80)])).toBe(40);
  });

  it("is null, not NaN, when nothing is scored", () => {
    expect(meanScore([])).toBeNull();
    expect(meanScore([INCOMPLETE("Iceland")])).toBeNull();
  });
});

describe("byEsgDesc", () => {
  it("orders highest first without reordering its input", () => {
    const input = [OK("Chile", 62.5), OK("Sweden", 81.5)];
    const sorted = byEsgDesc(input);

    expect(sorted.map((c) => c.name)).toEqual(["Sweden", "Chile"]);
    expect(input.map((c) => c.name)).toEqual(["Chile", "Sweden"]);
  });

  it("leaves an incomplete country out of the ranking entirely", () => {
    const sorted = byEsgDesc([OK("Chile", 62.5), INCOMPLETE("Iceland"), OK("Japan", 77)]);

    expect(sorted.map((c) => c.name)).toEqual(["Japan", "Chile"]);
  });

  it("keeps a real 0 in the ranking", () => {
    expect(byEsgDesc([OK("Zero", 0, "lagging"), OK("Chile", 62.5)]).map((c) => c.name)).toEqual(["Chile", "Zero"]);
  });
});

// A scored country whose input is first too old on 2027-01-01 (UTC). That date is not usable.
const EXPIRING = (over: Partial<MapCountry> = {}) => country({
  name: "Sweden", esg: 81.5, band: "leader", esg_status: "ok", esg_source: "pinned_table",
  earliest_expiry: "2027-01-01", ...over,
});

describe("utcDayStartMs", () => {
  it("is 00:00:00.000Z on the date", () => {
    expect(utcDayStartMs("2027-01-01")).toBe(Date.parse("2027-01-01T00:00:00.000Z"));
  });

  it("refuses anything that is not a real ISO calendar date", () => {
    expect(utcDayStartMs("2027-02-30")).toBeNull();
    expect(utcDayStartMs("2027-13-01")).toBeNull();
    expect(utcDayStartMs("0050-01-01")).toBe(Date.parse("0050-01-01T00:00:00.000Z"));
    expect(utcDayStartMs("2027-1-1")).toBeNull();
    expect(utcDayStartMs("2027-01-01T00:00:00Z")).toBeNull();
    expect(utcDayStartMs("")).toBeNull();
    expect(utcDayStartMs(null)).toBeNull();
    expect(utcDayStartMs(undefined)).toBeNull();
  });
});

describe("nextUtcMidnightAfter", () => {
  it("is the next 00:00Z strictly after the instant", () => {
    expect(nextUtcMidnightAfter(Date.parse("2026-12-31T23:59:59.999Z"))).toBe(Date.parse("2027-01-01T00:00:00.000Z"));
    expect(nextUtcMidnightAfter(Date.parse("2027-01-01T00:00:00.000Z"))).toBe(Date.parse("2027-01-02T00:00:00.000Z"));
  });
});

describe("utcDaysUntil", () => {
  const E = "2027-01-01";

  it("counts whole days from the UTC calendar date of now, as the backend does", () => {
    expect(utcDaysUntil(E, Date.parse("2026-12-30T12:00:00Z"))).toBe(2);
    expect(utcDaysUntil(E, Date.parse("2026-12-31T00:00:00Z"))).toBe(1);
    expect(utcDaysUntil(E, Date.parse("2026-12-31T23:59:59.999Z"))).toBe(1);
    expect(utcDaysUntil(E, Date.parse("2027-01-01T00:00:00.000Z"))).toBe(0);
  });

  it("is negative once the date has passed", () => {
    expect(utcDaysUntil(E, Date.parse("2027-01-03T08:00:00Z"))).toBe(-2);
  });

  it("is null when there is no date to count to", () => {
    expect(utcDaysUntil(null, Date.parse("2027-01-01T00:00:00Z"))).toBeNull();
    expect(utcDaysUntil("2027-02-30", Date.parse("2027-01-01T00:00:00Z"))).toBeNull();
  });
});

describe("isScoreExpiredAt", () => {
  const E = Date.parse("2027-01-01T00:00:00.000Z");

  it("is false at 23:59:59.999Z the day before, and true at 00:00:00.000Z on the date", () => {
    expect(isScoreExpiredAt(EXPIRING(), E - 1)).toBe(false);
    expect(isScoreExpiredAt(EXPIRING(), E)).toBe(true);
    expect(isScoreExpiredAt(EXPIRING(), E + 5_000)).toBe(true);
  });

  it("is never true for a country with no score, however old its date", () => {
    expect(isScoreExpiredAt(INCOMPLETE("Iceland"), E + 1)).toBe(false);
  });

  it("is never true for a scored country with no expiry date", () => {
    expect(isScoreExpiredAt(OK("Sweden", 81.5), E + 1)).toBe(false);
  });
});

describe("withClientExpiry", () => {
  const BEFORE = Date.parse("2026-12-31T23:59:59.999Z");
  const AT = Date.parse("2027-01-01T00:00:00.000Z");

  it("returns a new array, and every country it does not withhold unchanged", () => {
    const later = EXPIRING({ name: "Chile", earliest_expiry: "2027-06-01" });
    const countries = [EXPIRING(), later, INCOMPLETE("Iceland")];
    const view = withClientExpiry(countries, BEFORE);

    expect(view).not.toBe(countries);
    expect(view[0]).toBe(countries[0]);
    expect(view[1]).toBe(later);
    expect(view[2]).toBe(countries[2]);
  });

  it("withholds a scored country whose expiry has been reached, and marks it with its date", () => {
    const [sweden] = withClientExpiry([EXPIRING()], AT);

    expect(sweden.esg).toBeNull();
    expect(sweden.band).toBeNull();
    expect(scoreOf(sweden)).toBeNull();
    expect(statusOf(sweden)).toBe("incomplete");
    expect(sweden.client_expired).toBe(true);
    expect(sweden.client_expired_on).toBe("2027-01-01");
  });

  it("leaves a withheld country out of the ranking, the average and the leaders", () => {
    const view = withClientExpiry([EXPIRING(), EXPIRING({ name: "Chile", esg: 62.5, band: "developing",
      earliest_expiry: "2027-06-01" })], AT);
    const scope = aggregateScopeOf(view);

    expect(byEsgDesc(scope.included).map((c) => c.name)).toEqual(["Chile"]);
    expect(meanScore(scope.included)).toBe(63);
    expect(scope.included.filter((c) => c.band === "leader")).toEqual([]);
  });
});

describe("expiredSinceFetch", () => {
  const T = (iso: string) => Date.parse(iso);

  it("names the earliest passed date the data was fetched before", () => {
    const fetchedAt = T("2026-12-31T23:59:50Z");
    expect(expiredSinceFetch(["2027-02-01", "2027-01-01"], T("2027-01-01T00:00:05Z"), fetchedAt)).toBe("2027-01-01");
  });

  it("is null once the data was fetched at or after the date, so a fresh answer is not refetched", () => {
    expect(expiredSinceFetch(["2027-01-01"], T("2027-01-01T00:00:05Z"), T("2027-01-01T00:00:00.000Z"))).toBeNull();
    expect(expiredSinceFetch(["2027-01-01"], T("2027-01-01T00:00:05Z"), T("2027-01-01T00:00:01Z"))).toBeNull();
  });

  it("is null before the date", () => {
    expect(expiredSinceFetch(["2027-01-01"], T("2026-12-31T23:59:59.999Z"), T("2026-12-01T00:00:00Z"))).toBeNull();
  });
});

describe("expiryDatesOf", () => {
  it("collects the table's date and each scored country's, without repeats or incomplete countries", () => {
    const dates = expiryDatesOf(
      [EXPIRING(), EXPIRING({ name: "Chile", earliest_expiry: "2027-06-01" }),
        INCOMPLETE("Iceland"), EXPIRING({ name: "Japan", earliest_expiry: "2027-01-01" })],
      "2027-01-01",
    );
    expect(dates.sort()).toEqual(["2027-01-01", "2027-06-01"]);
  });
});
