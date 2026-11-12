import { expect, it } from "vitest";
import { formatSavedAt } from "./formatSavedAt";

it("shows the confirmed UTC instant and preserves the unknown legacy clock", () => {
  const created_at = "2026-11-09T22:44:26";
  expect(formatSavedAt({ created_at, saved_at_utc: "2026-11-09T19:44:26Z" }))
    .toMatch(/19:44:26 UTC$/);
  expect(formatSavedAt({ created_at, saved_at_utc: null }))
    .toBe("2026-11-09 22:44:26 (timezone unknown)");
  expect(formatSavedAt({ created_at: null })).toBe("—");
});
