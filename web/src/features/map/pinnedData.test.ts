/**
 * The pinned-data rules the map shows (C8 part 3): why an input is missing, when the
 * data expires, and when that expiry is close enough to show a notice.
 *
 * "No observation" and "observation too old" are different failures with different
 * fixes, so they must never read the same. The notice threshold is 60 days, and the
 * backend decides it; these tests pin what the page says for each side of it.
 */
import { describe, expect, it } from "vitest";

import { describeInput, expiryNoticeText, reasonSentence, type InputProvenance } from "./countryData";

describe("reasonSentence", () => {
  it("says a missing observation is a gap in the source, with no year", () => {
    expect(reasonSentence("gini_index", { code: "no_observation" })).toBe(
      "Gini index: no observation in the pinned source",
    );
  });

  it("says an observation is too old, with its year, its age and the limit", () => {
    expect(
      reasonSentence("hdi", {
        code: "too_old", year: "2023", age_years: 4, max_age_years: 3, expires_at: "2027-01-01",
      }),
    ).toBe("human development index: observation too old (2023, 4 years old; the limit is 3 years)");
  });

  it("never reads a too-old value as a missing one, nor the reverse", () => {
    const missing = reasonSentence("gini_index", { code: "no_observation" });
    const old = reasonSentence("gini_index", { code: "too_old", year: "2015", age_years: 11, max_age_years: 10 });

    expect(missing).not.toContain("too old");
    expect(old).not.toContain("no observation");
  });

  it("falls back to the reason's own sentence for any other code", () => {
    expect(reasonSentence("hdi", { code: "table_unavailable", text: "pinned input table unavailable: gone" }))
      .toBe("human development index: pinned input table unavailable: gone");
  });
});

describe("describeInput with expiry", () => {
  const p: InputProvenance = {
    value: 0.937, unit: "index, 0-1", year: "2023", age_years: 3, max_age_years: 3,
    expires_at: "2027-01-01", source: "UNDP", release: null, fetched_at: null,
  };

  it("says how long the value is usable", () => {
    expect(describeInput("hdi", p).text).toBe("0.937 index, 0-1, 2023, UNDP, usable until 2027-01-01");
  });
});

describe("expiryNoticeText", () => {
  it("is null when the notice is off, however close the date", () => {
    expect(expiryNoticeText("2027-01-01", 10, false)).toBeNull();
  });

  it("is null when there is no date to report", () => {
    expect(expiryNoticeText(null, null, true)).toBeNull();
  });

  it("gives the date and the days left when the expiry is near", () => {
    expect(expiryNoticeText("2027-01-01", 30, true)).toBe(
      "The pinned data expires on 2027-01-01, in 30 days. Scores that need an expired input go incomplete then. "
        + "A newer snapshot must be pinned before that date.",
    );
  });

  it("says today on the day itself", () => {
    expect(expiryNoticeText("2027-01-01", 0, true)).toContain("expires on 2027-01-01, today.");
  });

  it("says the data has expired once the date has passed, and what that means", () => {
    const text = expiryNoticeText("2027-01-01", -3, true) ?? "";
    expect(text).toContain("expired on 2027-01-01");
    expect(text).toContain("incomplete");
    expect(text).not.toContain("expires on");
  });
});
