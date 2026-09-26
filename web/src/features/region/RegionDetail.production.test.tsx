/**
 * RegionDetail against the production path (#218).
 *
 * RegionDetail has no mock path — it always calls `fetch` directly and never
 * consults `isMock`. The `isMock` guard is kept because #218 asks every
 * production test to state its mode. This test stubs the fetch and verifies the
 * page renders the server's response correctly. The payload is the real
 * production response captured 2026-09-26.
 *
 * What matters most here: the page must not show a constant as "67% confidence".
 * `confidence` is `len(distinct source names) / 3` and the required metric set
 * spans exactly two sources — so it is 0.67 for all 85 regions on every input.
 * The card was fixed in #395 and the tooltip in this PR, and this test verifies
 * the detail page shows the count ("2 из 3 ожидаемых") without a percentage.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import RegionDetail from "./RegionDetail";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { callsOf, stubJson } from "@/test/http";

/** Production response from GET /api/v1/map/russia/RU-MOW on 2026-09-26. */
const REGION_MOW_RESPONSE = {
  region: { code: "RU-MOW", name: "RU-MOW" },
  esg: { score: 89.25, e_score: 89.0, s_score: 86.5, g_score: 93.5 },
  confidence: 0.67,
  sources_used: [],
  sources_missing: [],
  model_version: null,
  computed_at: "2026-09-02T20:26:28.091744+00:00",
  features: null,
  indicators: [],
  indicators_count: 0,
  signals_total: 0,
  generated_at: "2026-09-26T16:19:58.225816+00:00",
  score_kind: "structural",
  score_vintage: "2024",
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("RegionDetail on the production path", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches the region from the API", async () => {
    const fetchStub = stubJson(REGION_MOW_RESPONSE);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    const [call] = callsOf(fetchStub);
    expect(call.url).toBe("/api/v1/map/russia/RU-MOW");
  });

  it("does not show the source count as a confidence percentage", async () => {
    stubJson(REGION_MOW_RESPONSE);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });
    const page = document.body.textContent ?? "";

    expect(page, "0.67 is len(sources)/3 for every region; as '67%' it reads as certainty")
      .not.toContain("67%");
    expect(page, "the Confidence card was removed").not.toContain("Confidence");
  });

  it("shows the source count as '2 из 3 ожидаемых'", async () => {
    stubJson(REGION_MOW_RESPONSE);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    expect(screen.getByText(/2 из 3 ожидаемых/)).toBeInTheDocument();
  });

  it("shows the score kind and vintage", async () => {
    stubJson(REGION_MOW_RESPONSE);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    expect(screen.getByText(/Структурный индекс/)).toBeInTheDocument();
    expect(screen.getByText(/данные 2024/)).toBeInTheDocument();
  });

  it("does not render the Sources chips card when both arrays are empty", async () => {
    stubJson(REGION_MOW_RESPONSE);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    // The Sources chips card should not be present
    const chips = document.querySelectorAll(".rd-chip");
    expect(chips.length).toBe(0);
  });

  it("renders the Sources chips when sources_used has entries", async () => {
    stubJson({ ...REGION_MOW_RESPONSE, sources_used: ["rosstat"] });

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    // The Sources card is hidden only when both lists are empty, so with a source present it renders
    const chip = document.querySelector(".rd-chip");
    expect(chip).toBeInTheDocument();
    expect(chip?.textContent).toContain("rosstat");
  });

  it("renders the total score in the exact format RegionDetail uses", async () => {
    stubJson(REGION_MOW_RESPONSE);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    // 89.25 renders as 89.3 because toFixed(1)
    await screen.findByText("89.3", { exact: false });

    expect(screen.getByText("89.3")).toBeInTheDocument();
  });
});
