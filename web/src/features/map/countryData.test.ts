/**
 * The rules the map's numbers stand on. The page tests exercise these through
 * the rendered page; these pin the rules themselves, including the cases the
 * page fixtures do not reach.
 */
import { describe, expect, it } from "vitest";

import {
  aggregateScopeOf,
  byEsgDesc,
  measuredPhysicalOf,
  sourceKindOf,
  type MapCountry,
} from "./countryData";

const country = (over: Partial<MapCountry>): MapCountry => ({
  code: "XX", name: "X", lat: 0, lon: 0, esg: 50, band: "emerging", ...over,
});

const LIVE = (name: string, esg: number, band = "developing") =>
  country({ name, esg, band, esg_source: "world_bank_live" });
const STATIC = (name: string, esg: number, band = "leader") =>
  country({ name, esg, band, esg_source: "hardcoded" });

describe("sourceKindOf", () => {
  it("maps the two tokens the backend sends, and nothing else to a known kind", () => {
    expect(sourceKindOf("world_bank_live")).toBe("live");
    expect(sourceKindOf("hardcoded")).toBe("static");
    expect(sourceKindOf(undefined)).toBe("unknown");
    expect(sourceKindOf(null)).toBe("unknown");
    expect(sourceKindOf("mock")).toBe("unknown");
  });
});

describe("measuredPhysicalOf", () => {
  it("reports nothing for a static profile, even when the payload carries numbers", () => {
    // The backend derives these from the ESG score for a static profile.
    const v = measuredPhysicalOf(
      country({ esg_source: "hardcoded", renewable_share_pct: 81.7, co2_intensity_t_per_capita: 4.52 }),
    );
    expect(v).toEqual({ renewable_share_pct: null, co2_intensity_t_per_capita: null });
  });

  it("reports the values of a live country", () => {
    const v = measuredPhysicalOf(
      country({ esg_source: "world_bank_live", renewable_share_pct: 60.1, co2_intensity_t_per_capita: 3.6 }),
    );
    expect(v).toEqual({ renewable_share_pct: 60.1, co2_intensity_t_per_capita: 3.6 });
  });

  it("reports null for a live country's non-finite value, not NaN", () => {
    const v = measuredPhysicalOf(
      country({ esg_source: "world_bank_live", renewable_share_pct: Number.NaN, co2_intensity_t_per_capita: undefined }),
    );
    expect(v).toEqual({ renewable_share_pct: null, co2_intensity_t_per_capita: null });
  });

  it("reports nothing for a country whose source is unknown", () => {
    const v = measuredPhysicalOf(country({ renewable_share_pct: 50, co2_intensity_t_per_capita: 2 }));
    expect(v).toEqual({ renewable_share_pct: null, co2_intensity_t_per_capita: null });
  });
});

describe("aggregateScopeOf", () => {
  it("has no aggregate over an empty list", () => {
    const s = aggregateScopeOf([]);
    expect(s.included).toEqual([]);
    expect(s.label).toBe("no countries");
  });

  it("uses only the live countries when both kinds are present, and states N of M", () => {
    const countries = [STATIC("Iceland", 86), LIVE("Sweden", 81.5, "leader"), LIVE("Chile", 62.5)];
    const s = aggregateScopeOf(countries);

    expect(s.included.map((c) => c.name)).toEqual(["Sweden", "Chile"]);
    expect(s.total).toBe(3);
    expect(s.label).toBe("live source only (world_bank_live): N = 2 of 3 countries");
  });

  it("states N as a plain count when every country is live", () => {
    const s = aggregateScopeOf([LIVE("Sweden", 81.5), LIVE("Chile", 62.5)]);
    expect(s.included).toHaveLength(2);
    expect(s.label).toBe("live source only (world_bank_live): N = 2 countries");
  });

  it("uses every country, and says so, when all of them are static", () => {
    const s = aggregateScopeOf([STATIC("Iceland", 86), STATIC("Japan", 77)]);
    expect(s.included).toHaveLength(2);
    expect(s.label).toBe("static reference values only: N = 2 countries");
  });

  it("says mixed, and uses every country, when a source is missing", () => {
    const s = aggregateScopeOf([
      LIVE("Sweden", 81.5),
      country({ name: "Chile", esg: 62.5 }),
    ]);
    expect(s.included).toHaveLength(2);
    expect(s.label).toContain("mixed or unlabelled sources");
    expect(s.label).toContain("N = 2 countries");
  });
});

describe("byEsgDesc", () => {
  it("orders highest first without reordering its input", () => {
    const input = [LIVE("Chile", 62.5), LIVE("Sweden", 81.5)];
    const sorted = byEsgDesc(input);

    expect(sorted.map((c) => c.name)).toEqual(["Sweden", "Chile"]);
    expect(input.map((c) => c.name)).toEqual(["Chile", "Sweden"]);
  });
});
