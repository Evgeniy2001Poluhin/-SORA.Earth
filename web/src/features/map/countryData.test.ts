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
  measuredPhysicalOf,
  meanScore,
  scoreOf,
  sourceLabelOf,
  statusOf,
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
