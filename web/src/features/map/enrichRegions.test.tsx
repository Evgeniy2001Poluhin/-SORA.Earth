/**
 * The enrichment path from GET /api/v1/map/russia to the region card.
 *
 * Measured 2026-09-26: the modal's useMemo copied `esgScore`, `esgBreakdown`,
 * `confidence`, `sourcesUsed`, `updatedAt` from the API regions but NOT
 * `score_kind` or `score_vintage`, so `selected.scoreKind` was always undefined
 * and the card's "Структурный индекс, данные 2024" note never rendered. The live
 * page (RU-YAN, ESG mode) showed "Источников 2 из 3 ожидаемых" and nothing above
 * it; the mock warning can never appear either.
 *
 * The card's own tests (`RegionEsgCard.test.tsx`) pass the kind directly, which
 * is why nobody saw it; the modal itself cannot be rendered in jsdom
 * (react-leaflet). Extracting the enrichment into two pure functions makes the
 * full path testable.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { enrichRegions, cardPropsOf } from "./enrichRegions";
import { RUSSIA_REGIONS } from "@/data/russia_regions";
import type { Region } from "@/hooks/useRussiaMap";
import RegionEsgCard from "./RegionEsgCard";

/** A real list item from production, 2026-09-26 (the API sends no name/capital). */
const RU_AST_API: Region = {
  code: "RU-AST",
  name: "",
  capital: "",
  district: "",
  lat: 0,
  lon: 0,
  population: 0,
  esg: { score: 66.73, e_score: 79.5, s_score: 48.38, g_score: 72.0 },
  confidence: 0.67,
  sources_used: [],
  updated_at: "2026-09-02T20:26:28.091744+00:00",
  score_kind: "structural",
  score_vintage: "2024",
  stale_since: null,
};

describe("enrichRegions", () => {
  it("merges the RU-AST item with the static region", () => {
    const enriched = enrichRegions([RU_AST_API]);
    const ast = enriched.find((r) => r.code === "RU-AST");

    expect(ast).toBeDefined();
    expect(ast!.scoreKind).toBe("structural");
    expect(ast!.scoreVintage).toBe("2024");
    expect(ast!.confidence).toBe(0.67);
    expect(ast!.esgScore).toBe(66.73);
    // The static name from RUSSIA_REGIONS, not the empty API name:
    expect(ast!.name).toBe("Астраханская область");
  });

  it("returns RUSSIA_REGIONS unchanged when API regions is undefined", () => {
    const enriched = enrichRegions(undefined);
    expect(enriched).toBe(RUSSIA_REGIONS);
  });

  it("returns RUSSIA_REGIONS unchanged when API regions is empty", () => {
    const enriched = enrichRegions([]);
    expect(enriched).toBe(RUSSIA_REGIONS);
  });

  it("keeps a static region unchanged when absent from the API list", () => {
    // Only RU-AST in the API, so RU-MOW should be unchanged:
    const enriched = enrichRegions([RU_AST_API]);
    const mow = enriched.find((r) => r.code === "RU-MOW");

    expect(mow).toBeDefined();
    expect(mow!.scoreKind).toBeUndefined();
    expect(mow!.scoreVintage).toBeUndefined();
  });
});

describe("the full path from API to card", () => {
  it("shows the structural index note for a structural score", () => {
    const enriched = enrichRegions([RU_AST_API]);
    const ast = enriched.find((r) => r.code === "RU-AST")!;
    const { container } = render(
      <MemoryRouter>
        <RegionEsgCard {...cardPropsOf(ast)} />
      </MemoryRouter>
    );
    const text = container.textContent ?? "";

    expect(text).toContain("Структурный индекс");
    expect(text).toContain("2024");
  });

  it("shows the mock warning for a mock score", () => {
    const mockItem: Region = { ...RU_AST_API, score_kind: "mock", score_vintage: null };
    const enriched = enrichRegions([mockItem]);
    const ast = enriched.find((r) => r.code === "RU-AST")!;
    const { container } = render(
      <MemoryRouter>
        <RegionEsgCard {...cardPropsOf(ast)} />
      </MemoryRouter>
    );
    const text = container.textContent ?? "";

    expect(text).toContain("Заглушка");
    expect(text).toContain("данных по региону нет");
  });

  it("shows the stale warning when stale_since is set", () => {
    const staleItem: Region = { ...RU_AST_API, stale_since: "2026-09-20T00:00:00Z" };
    const enriched = enrichRegions([staleItem]);
    const ast = enriched.find((r) => r.code === "RU-AST")!;
    const { container } = render(
      <MemoryRouter>
        <RegionEsgCard {...cardPropsOf(ast)} />
      </MemoryRouter>
    );
    const text = container.textContent ?? "";

    expect(text).toContain("Не подтверждено с");
    expect(text).toContain("источники не присылали данные");
  });

  it("does not show stale warning when stale_since is null", () => {
    const freshItem: Region = { ...RU_AST_API, stale_since: null };
    const enriched = enrichRegions([freshItem]);
    const ast = enriched.find((r) => r.code === "RU-AST")!;
    const { container } = render(
      <MemoryRouter>
        <RegionEsgCard {...cardPropsOf(ast)} />
      </MemoryRouter>
    );
    const text = container.textContent ?? "";

    expect(text).not.toContain("Не подтверждено с");
  });
});

describe("enrichRegions carries stale_since", () => {
  it("copies stale_since from the API item", () => {
    const staleItem: Region = { ...RU_AST_API, stale_since: "2026-09-20T00:00:00Z" };
    const enriched = enrichRegions([staleItem]);
    const ast = enriched.find((r) => r.code === "RU-AST")!;

    expect(ast.staleSince).toBe("2026-09-20T00:00:00Z");
  });

  it("sets staleSince to null when stale_since is null", () => {
    const freshItem: Region = { ...RU_AST_API, stale_since: null };
    const enriched = enrichRegions([freshItem]);
    const ast = enriched.find((r) => r.code === "RU-AST")!;

    expect(ast.staleSince).toBeNull();
  });
});

describe("cardPropsOf carries code for the detail page link", () => {
  it("includes the region code", () => {
    const enriched = enrichRegions([RU_AST_API]);
    const ast = enriched.find((r) => r.code === "RU-AST")!;
    const props = cardPropsOf(ast);

    expect(props.code).toBe("RU-AST");
  });
});

describe("population precedence (F076)", () => {
  // Moscow is in the static list with a population; the API item is keyed onto it.
  const STATIC_MOW = RUSSIA_REGIONS.find((r) => r.code === "RU-MOW")!.population;
  const moscow = (item: Region) => enrichRegions([{ ...item, code: "RU-MOW" }]).find((r) => r.code === "RU-MOW")!;

  it("takes a population the API sends over the static value", () => {
    expect(STATIC_MOW).not.toBe(13500000);
    expect(moscow({ ...RU_AST_API, population: 13500000 }).population).toBe(13500000);
  });

  it("keeps the static value when the API sends no population", () => {
    expect(moscow({ ...RU_AST_API, population: 0 }).population).toBe(STATIC_MOW);
  });

  it("keeps the static value for a mock row, whose population is a placeholder", () => {
    // The mock rows carry their own hardcoded populations (`score_kind: "mock"`).
    // Those are not a source, so they must not replace the static one.
    expect(moscow({ ...RU_AST_API, population: 13010112, score_kind: "mock" }).population).toBe(STATIC_MOW);
  });
});
