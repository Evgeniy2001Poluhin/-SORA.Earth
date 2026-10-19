import { describe, expect, it } from "vitest";
import { bandLabel } from "./forecastLabels";

const CAVEAT = "model-based, coverage not validated";

describe("bandLabel: the level is named only when the server states it (C16)", () => {
  it("names a 90% level as nominal 90%", () => {
    expect(bandLabel(0.9)).toBe(`nominal 90% interval (${CAVEAT})`);
  });

  it("names a 95% level as nominal 95%", () => {
    expect(bandLabel(0.95)).toBe(`nominal 95% interval (${CAVEAT})`);
  });

  it("calls a band with a null level a neutral model interval", () => {
    expect(bandLabel(null)).toBe(`model interval (${CAVEAT})`);
  });

  it("calls a band with a missing level (older responses) a neutral model interval", () => {
    expect(bandLabel(undefined)).toBe(`model interval (${CAVEAT})`);
  });

  it("never names a level it was not given", () => {
    for (const level of [null, undefined]) {
      expect(bandLabel(level)).not.toMatch(/nominal \d+%/);
    }
  });
});
