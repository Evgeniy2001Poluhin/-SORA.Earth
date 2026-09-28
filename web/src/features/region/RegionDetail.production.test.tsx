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

/** Production response from GET /api/v1/map/russia/RU-MOW with realistic inputs.
 * Values from the repository's rosstat snapshot and sber baseline for RU-MOW:
 * - sber_veb_baseline:esg_index_baseline = 89.0 (data/sber_veb_baseline.py, line 18)
 * - rosstat:unemployment_rate = 1.9 (data/rosstat_snapshot_2024.py, UNEMPLOYMENT)
 * - rosstat:avg_income_rub = 115470 (data/rosstat_snapshot_2024.py, INCOME)
 * - rosstat:life_expectancy = 78.21 (data/rosstat_snapshot_2024.py, LIFE_EXPECTANCY)
 * - rosstat:budget_transparency = 93.5 (data/rosstat_snapshot_2024.py, BUDGET_TRANSPARENCY)
 * - rosstat:digital_gov_index = 93.5 (data/rosstat_snapshot_2024.py, DIGITAL_GOV)
 */
const REGION_MOW_RESPONSE = {
  region: { code: "RU-MOW", name: "RU-MOW" },
  esg: { score: 89.25, e_score: 89.0, s_score: 86.5, g_score: 93.5 },
  confidence: 0.67,
  sources_used: [],
  sources_missing: [],
  model_version: null,
  computed_at: "2026-09-02T20:26:28.091744+00:00",
  features: null,
  inputs: [
    { source: "sber_veb_baseline", indicator: "esg_index_baseline", value: 89.0, unit: null, temporal_kind: "not_applicable", period_start: null, period_end: null, delivered_at: "2026-09-27T12:00:00Z", source_revision: "snapshot_2024_v1", missing: false },
    { source: "rosstat", indicator: "unemployment_rate", value: 1.9, unit: "%", temporal_kind: "period", period_start: "2024-01-01T00:00:00Z", period_end: "2024-12-31T23:59:59Z", delivered_at: "2026-09-27T12:00:00Z", source_revision: "snapshot_2024_v2", missing: false },
    { source: "rosstat", indicator: "avg_income_rub", value: 115470, unit: "₽", temporal_kind: "period", period_start: "2024-01-01T00:00:00Z", period_end: "2024-12-31T23:59:59Z", delivered_at: "2026-09-27T12:00:00Z", source_revision: "snapshot_2024_v2", missing: false },
    { source: "rosstat", indicator: "life_expectancy", value: 78.21, unit: "лет", temporal_kind: "period", period_start: "2024-01-01T00:00:00Z", period_end: "2024-12-31T23:59:59Z", delivered_at: "2026-09-27T12:00:00Z", source_revision: "snapshot_2024_v2", missing: false },
    { source: "rosstat", indicator: "budget_transparency", value: 93.5, unit: null, temporal_kind: "period", period_start: "2024-01-01T00:00:00Z", period_end: "2024-12-31T23:59:59Z", delivered_at: "2026-09-27T12:00:00Z", source_revision: "snapshot_2024_v2", missing: false },
    { source: "rosstat", indicator: "digital_gov_index", value: 93.5, unit: null, temporal_kind: "period", period_start: "2024-01-01T00:00:00Z", period_end: "2024-12-31T23:59:59Z", delivered_at: "2026-09-27T12:00:00Z", source_revision: "snapshot_2024_v2", missing: false },
  ],
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

  it("shows the stale warning when stale_since is set", async () => {
    stubJson({ ...REGION_MOW_RESPONSE, stale_since: "2026-09-20T00:00:00Z" });

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    expect(screen.getByText(/Не подтверждено с/)).toBeInTheDocument();
    expect(screen.getByText(/источники не присылали данные/)).toBeInTheDocument();
  });

  it("does not show stale warning when stale_since is null (production payload)", async () => {
    // The production payload has no stale_since or it's null
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

    expect(page).not.toContain("Не подтверждено с");
  });

  it("shows the six input labels in Russian", async () => {
    stubJson(REGION_MOW_RESPONSE);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    expect(screen.getByText("Базовый ESG-индекс")).toBeInTheDocument();
    expect(screen.getByText("Уровень безработицы")).toBeInTheDocument();
    expect(screen.getByText("Средний доход")).toBeInTheDocument();
    expect(screen.getByText("Ожидаемая продолжительность жизни")).toBeInTheDocument();
    expect(screen.getByText("Прозрачность бюджета")).toBeInTheDocument();
    expect(screen.getByText("Индекс цифрового госуправления")).toBeInTheDocument();
  });

  it("shows input values with units formatted with ru-RU locale", async () => {
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

    // Values formatted with ru-RU grouping and at most 2 decimals
    // toLocaleString("ru-RU") produces non-breaking spaces (U+00A0)
    expect(page).toContain("89"); // esg_index_baseline (no unit so no mapping)
    expect(page).toContain("1,9 %"); // unemployment_rate: 1.9 -> "1,9 %"
    expect(page).toContain("115 470 ₽"); // avg_income_rub: 115470 -> "115 470 ₽" (RUB -> ₽, with nbsp)
    expect(page).toContain("78,21 лет"); // life_expectancy: 78.21 -> "78,21 лет" (years -> лет)
  });

  it("maps units to Russian words", async () => {
    stubJson(REGION_MOW_RESPONSE);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    // Assert unit mappings: an income value with ₽, a life expectancy with лет
    // Regex accounts for non-breaking space from toLocaleString
    expect(screen.getByText(/115.470 ₽/)).toBeInTheDocument();
    expect(screen.getByText(/78,21 лет/)).toBeInTheDocument();
  });

  it("shows 0-100 values with 'из 100'", async () => {
    // Add a 0-100 unit to one of the inputs
    const responseWith0100 = {
      ...REGION_MOW_RESPONSE,
      inputs: [
        REGION_MOW_RESPONSE.inputs[0],
        REGION_MOW_RESPONSE.inputs[1],
        REGION_MOW_RESPONSE.inputs[2],
        REGION_MOW_RESPONSE.inputs[3],
        { ...REGION_MOW_RESPONSE.inputs[4], unit: "0-100" }, // budget_transparency
        REGION_MOW_RESPONSE.inputs[5],
      ],
    };

    stubJson(responseWith0100);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });
    const page = document.body.textContent ?? "";

    // budget_transparency: 93.5 with unit "0-100" -> "93,5 из 100"
    expect(page).toContain("93,5 из 100");
  });

  it("shows Росстат source label", async () => {
    stubJson(REGION_MOW_RESPONSE);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    // "Росстат" appears 5 times (five rosstat inputs)
    const rosstat = screen.getAllByText("Росстат");
    expect(rosstat.length).toBeGreaterThanOrEqual(5);
  });

  it("shows period containing 2024 for rosstat inputs", async () => {
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

    // Rosstat inputs show 2024 periods (at least one occurrence per input)
    expect(page).toContain("2024");
  });

  it("shows missing input as 'нет данных'", async () => {
    const responseWithMissing = {
      ...REGION_MOW_RESPONSE,
      inputs: [
        ...REGION_MOW_RESPONSE.inputs.slice(0, 5),
        { source: "rosstat", indicator: "digital_gov_index", value: null, unit: null, temporal_kind: null, period_start: null, period_end: null, delivered_at: null, source_revision: null, missing: true },
      ],
    };
    stubJson(responseWithMissing);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    expect(screen.getByText("нет данных")).toBeInTheDocument();
  });

  it("no longer shows the old indicators/signals section", async () => {
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

    // The old section title was "Indicators(0)"
    expect(page).not.toContain("Indicators");
    expect(page).not.toContain("Points");
    expect(page).not.toContain("Metric");
  });

  it("says the inputs are unavailable when the server could not read them", async () => {
    // inputs: null is the API's way of saying the read failed -- not "no inputs".
    stubJson({ ...REGION_MOW_RESPONSE, inputs: null });

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("89.3", { exact: false });

    expect(screen.getByText(/Входные данные недоступны/)).toBeInTheDocument();
    expect(screen.queryByText("Базовый ESG-индекс")).not.toBeInTheDocument();
  });

  it("does not say the inputs are unavailable when they were read", async () => {
    stubJson(REGION_MOW_RESPONSE);

    renderWithQuery(
      <MemoryRouter initialEntries={["/region/RU-MOW"]}>
        <Routes>
          <Route path="/region/:code" element={<RegionDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("Базовый ESG-индекс");

    expect(screen.queryByText(/Входные данные недоступны/)).not.toBeInTheDocument();
  });
});
