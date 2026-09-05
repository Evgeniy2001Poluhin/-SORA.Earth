/**
 * The map against a successful-but-empty answer, and against a legitimately
 * empty one (#236).
 *
 * Two different failures live here. `{}` crashed on `data.countries.filter`,
 * the same shape as the rest of the sweep. But `{countries: []}` — a perfectly
 * valid answer meaning "no countries yet" — crashed on `countries[0].name`,
 * which needs no malformed payload at all.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import MapPage from "./MapPage";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { stubJson } from "@/test/http";

const render = () => renderWithQuery(<MemoryRouter><MapPage /></MemoryRouter>);

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("MapPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing rather than crashing on an empty 200", async () => {
    // A guarded component returning null and a crashed one both leave an
    // empty container, so the DOM cannot tell them apart -- the first version
    // of this test asserted on the DOM and survived its own mutation, which
    // is how it was caught. The thrown error is the only observable
    // difference, so that is what is asserted.
    stubJson({});
    const thrown: string[] = [];
    vi.spyOn(console, "error").mockImplementation((...args) => {
      thrown.push(args.map(String).join(" "));
    });

    render();
    await new Promise((resolve) => setTimeout(resolve, 400));

    expect(thrown.filter((m) => /TypeError/.test(m))).toEqual([]);
  });

  it("stays up when the server legitimately reports no countries", async () => {
    stubJson({ total_countries: 0, countries: [], bands: {} });

    const { container } = render();

    // Not the `.map-page` selector: the loading state carries that class
    // too, so the wait would resolve before the payload ever arrived.
    await waitFor(() => expect(container.textContent ?? "").toContain("0 countries"), {
      timeout: 3000,
    });
    const text = container.textContent ?? "";
    // Averaging an empty list divides by zero.
    expect(text).not.toContain("NaN");
  });

  it("still shows the leader stats when countries are present", async () => {
    stubJson({
      total_countries: 2,
      countries: [
        { name: "Sweden", esg: 81.5, band: "leader", code: "SE", lat: 60, lon: 15 },
        { name: "Chile", esg: 62.5, band: "mid", code: "CL", lat: -33, lon: -70 },
      ],
      bands: {},
    });

    const { container } = render();

    await waitFor(() => expect(container.textContent ?? "").toContain("Sweden"), { timeout: 3000 });
    expect(container.textContent ?? "").toContain("72");
  });
});
