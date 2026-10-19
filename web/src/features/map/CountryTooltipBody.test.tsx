/**
 * The country tooltip shows what each number is, where it came from, and when a
 * country has no score. C8: an incomplete country says so and names what is missing;
 * it never shows a number in place of the score. A scored country shows each input's
 * value, year and source.
 *
 * Tested through the extracted body: react-leaflet's Tooltip is a layer and is
 * not in the DOM until a marker is opened in a real map.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import CountryTooltipBody from "./CountryTooltipBody";
import type { InputProvenance, MapCountry } from "./countryData";

const SWEDEN_INPUTS: Partial<Record<string, InputProvenance>> = {
  renewable_share: {
    value: 60.1, unit: "% (of total final energy consumption)", year: "2021", age_years: 5,
    max_age_years: 5, expires_at: "2027-01-01",
    source: "World Bank EG.FEC.RNEW.ZS (World Development Indicators)",
    release: "World Bank lastupdated 2026-10-08", fetched_at: "2026-10-18T13:07:20Z",
  },
  gini_index: {
    value: 27.7, unit: "index, 0-100 (survey-based, sparse years)", year: "2022", age_years: 4,
    source: "World Bank SI.POV.GINI (World Development Indicators)",
    release: "World Bank lastupdated 2026-10-08", fetched_at: "2026-10-18T13:07:16Z",
  },
};

// As the backend sends a country with a complete score from the pinned table.
const SWEDEN_OK: MapCountry = {
  code: "SE", name: "Sweden", lat: 60, lon: 15, esg: 81.5, band: "leader",
  esg_status: "ok", esg_source: "pinned_table", missing_inputs: [],
  inputs: SWEDEN_INPUTS,
  renewable_share_pct: 60.1, co2_intensity_t_per_capita: null,
};

// As the backend sends a country with a missing input: no score, names what is missing.
const ICELAND_INCOMPLETE: MapCountry = {
  code: "IS", name: "Iceland", lat: 65, lon: -19, esg: null, band: null,
  esg_status: "incomplete", esg_source: null,
  missing_inputs: ["gini_index", "gov_effectiveness"],
  renewable_share_pct: null, co2_intensity_t_per_capita: null,
};

// A real score of zero. It is a score, not a missing one.
const ZERO_OK: MapCountry = {
  code: "ZZ", name: "Zeroland", lat: 0, lon: 0, esg: 0, band: "lagging",
  esg_status: "ok", esg_source: "pinned_table", missing_inputs: [],
  renewable_share_pct: null, co2_intensity_t_per_capita: null,
};

// As /map/countries sends esg_source_meanings.
const MEANINGS: Record<string, string> = {
  pinned_table: "Every input is read from the pinned official table.",
};

function renderBody(country: MapCountry, meanings?: Record<string, string> | null) {
  return render(<CountryTooltipBody country={country} meanings={meanings} />);
}

describe("the country tooltip", () => {
  it("shows an incomplete country as incomplete, with what is missing, and no number", () => {
    const { container } = renderBody(ICELAND_INCOMPLETE, MEANINGS);
    const text = container.textContent ?? "";

    expect(text).toContain("Iceland");
    expect(text).toContain("incomplete");
    expect(text).toContain("no score");
    expect(text).toContain("Missing: Gini index, government effectiveness");
    // "CO2" carries a digit of its own; the check is for a score or a value, which would be digits.
    expect(text.replace("CO2", ""), "an incomplete country shows no score, no percentage").not.toMatch(/\d/);
    expect(text).not.toMatch(/NaN|null|undefined/);
  });

  it("gives no source line and no input list for an incomplete country", () => {
    const text = renderBody(ICELAND_INCOMPLETE, MEANINGS).container.textContent ?? "";
    expect(text).not.toContain("source:");
    expect(text).not.toContain("Gini index 27");
  });

  it("shows a real 0 score as 0, not as incomplete", () => {
    const { container } = renderBody(ZERO_OK, MEANINGS);
    const text = container.textContent ?? "";

    expect(text).toContain("Zeroland");
    expect(container.querySelector(".tip-score-num")?.textContent).toBe("0");
    expect(text).not.toContain("incomplete");
    expect(text).not.toContain("Missing");
  });

  it("lists each scored input with its value, year and source", () => {
    const { container } = renderBody(SWEDEN_OK, MEANINGS);
    const items = Array.from(container.querySelectorAll(".tip-inputs li")).map((li) => li.textContent ?? "");

    expect(items).toHaveLength(2);
    expect(items[0]).toContain("renewables share");
    expect(items[0]).toContain("60.1");
    expect(items[0]).toContain("2021");
    expect(items[0]).toContain("World Bank EG.FEC.RNEW.ZS");
    expect(items[1]).toContain("Gini index");
    expect(items[1]).toContain("27.7");
    expect(items[1]).toContain("2022");
  });

  it("does not label an ok score as mixed or unlabelled", () => {
    const text = renderBody(SWEDEN_OK, MEANINGS).container.textContent ?? "";
    expect(text).not.toContain("mixed");
  });

  it("shows a country's renewables, and no data for a value it lacks", () => {
    const text = renderBody(SWEDEN_OK, MEANINGS).container.textContent ?? "";

    expect(text).toContain("60.1%");
    expect(text).toContain("no data");
    expect((text.match(/no data/g) ?? []).length).toBe(1);
  });

  it("shows the backend's meaning in the source line when it sends one", () => {
    const text = renderBody(SWEDEN_OK, MEANINGS).container.textContent ?? "";

    expect(text).toContain("source: Every input is read from the pinned official table.");
  });

  it("falls back to the raw token when the response carries no meanings", () => {
    expect(renderBody(SWEDEN_OK).container.textContent).toContain("source: pinned_table");
  });

  it("names missing inputs by their human label", () => {
    const text = renderBody({ ...ICELAND_INCOMPLETE, missing_inputs: ["hdi"] }).container.textContent ?? "";
    expect(text).toContain("Missing: human development index");
  });

  it("gives the reason for each missing input, and tells no observation from observation too old", () => {
    const country: MapCountry = {
      ...ICELAND_INCOMPLETE,
      missing_inputs: ["gini_index", "hdi"],
      input_reasons: {
        gini_index: { code: "no_observation", text: "no observation in the pinned table" },
        hdi: { code: "too_old", year: "2023", age_years: 4, max_age_years: 3, expires_at: "2027-01-01" },
      },
    };
    const items = Array.from(renderBody(country, MEANINGS).container.querySelectorAll(".tip-reasons li"))
      .map((li) => li.textContent ?? "");

    expect(items).toEqual([
      "Gini index: no observation in the pinned source",
      "human development index: observation too old (2023, 4 years old; the limit is 3 years)",
    ]);
  });

  it("shows an input with no reason by its label, beside the reasons that exist", () => {
    const country: MapCountry = {
      ...ICELAND_INCOMPLETE,
      missing_inputs: ["gini_index", "hdi"],
      input_reasons: { gini_index: { code: "no_observation" } },
    };
    const text = renderBody(country, MEANINGS).container.textContent ?? "";

    expect(text).toContain("Gini index: no observation in the pinned source");
    expect(text).toContain("human development index");
  });

  it("shows the scored input's expiry in its line", () => {
    const text = renderBody(SWEDEN_OK, MEANINGS).container.textContent ?? "";
    expect(text).toContain("usable until");
  });

  it("says a withheld score is withheld, names the expiry date, and shows no number", () => {
    // What withClientExpiry returns for a score whose input expired (UTC) before the page's clock.
    const withheld: MapCountry = {
      ...SWEDEN_OK, esg: null, band: null, esg_status: "incomplete",
      client_expired: true, client_expired_on: "2027-01-01",
    };
    const { container } = renderBody(withheld, MEANINGS);
    const text = container.textContent ?? "";

    expect(text).toContain("Score withheld: an input expired on 2027-01-01 (UTC); waiting for fresh data.");
    expect(container.querySelector(".tip-score-num")?.textContent).toBe("withheld");
    expect(text).not.toContain("source:");
    expect(text).not.toContain("usable until");
    expect(text, "the withheld score is not shown").not.toContain("81.5");
  });
});
