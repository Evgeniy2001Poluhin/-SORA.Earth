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

/** A clock that is `days` whole UTC days before 2027-01-01 (negative: after it). */
const clockDaysBeforeExpiry = (days: number) => Date.parse("2027-01-01T12:00:00Z") - days * 86_400_000;

describe("expiryNoticeText", () => {
  it("is null when there is no date to report", () => {
    expect(expiryNoticeText(null, clockDaysBeforeExpiry(10), 60)).toBeNull();
  });

  it("is null when the date is further off than the notice window", () => {
    expect(expiryNoticeText("2027-01-01", clockDaysBeforeExpiry(61), 60)).toBeNull();
  });

  it("shows at the edge of the window, and the window defaults to 60 days when the payload omits it", () => {
    expect(expiryNoticeText("2027-01-01", clockDaysBeforeExpiry(60), 60)).not.toBeNull();
    expect(expiryNoticeText("2027-01-01", clockDaysBeforeExpiry(60))).not.toBeNull();
    expect(expiryNoticeText("2027-01-01", clockDaysBeforeExpiry(61))).toBeNull();
  });

  it("gives the date and the days left when the expiry is near", () => {
    expect(expiryNoticeText("2027-01-01", clockDaysBeforeExpiry(30), 60)).toBe(
      "The pinned data expires on 2027-01-01, in 30 days. Scores that need an expired input go incomplete then. "
        + "A newer snapshot must be pinned before that date.",
    );
  });

  it("reads one day as 'in 1 day', not 'in 1 days'", () => {
    expect(expiryNoticeText("2027-01-01", clockDaysBeforeExpiry(1), 60)).toContain("expires on 2027-01-01, in 1 day.");
  });

  it("says today on the day itself", () => {
    expect(expiryNoticeText("2027-01-01", clockDaysBeforeExpiry(0), 60)).toContain("expires on 2027-01-01, today.");
  });

  it("says the data has expired once the date has passed, and what that means", () => {
    const text = expiryNoticeText("2027-01-01", clockDaysBeforeExpiry(-3), 60) ?? "";
    expect(text).toContain("expired on 2027-01-01");
    expect(text).toContain("incomplete");
    expect(text).not.toContain("expires on");
  });
});
