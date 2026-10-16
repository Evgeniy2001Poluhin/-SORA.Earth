/**
 * The home page states only what is true.
 *
 * Finding 12 noted several false claims: six literal KPI tiles, "Stacked ML
 * model" in step 02 (it is a RandomForest), "32 countries" in the hero and
 * tour (27 in the evaluation list, 33 on the map), and "Same backend that
 * powers the live tile above. ~300ms per evaluation." (no live tile, 300ms
 * never measured). The page's claims are checked on the rendered page. The
 * tour's step text is checked by OnboardingTour.test.tsx, which walks all five
 * steps through the rendered tour.
 */
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HomePage } from "./HomePage";

// Mock three to prevent WebGL errors in jsdom
vi.mock("three", () => ({
  Scene: vi.fn(() => ({ add: vi.fn() })),
  PerspectiveCamera: vi.fn(() => ({ position: { set: vi.fn() }, aspect: 0, updateProjectionMatrix: vi.fn() })),
  WebGLRenderer: vi.fn(() => ({
    setSize: vi.fn(),
    setPixelRatio: vi.fn(),
    render: vi.fn(),
    dispose: vi.fn(),
    domElement: document.createElement("canvas"),
  })),
  Mesh: vi.fn(() => ({ add: vi.fn(), position: { set: vi.fn() }, rotation: { y: 0 } })),
  SphereGeometry: vi.fn(),
  MeshStandardMaterial: vi.fn(),
  LineBasicMaterial: vi.fn(),
  Line: vi.fn(() => ({ add: vi.fn() })),
  BufferGeometry: vi.fn(() => ({ setFromPoints: vi.fn(() => ({ setFromPoints: vi.fn() })) })),
  Vector3: vi.fn((x: number, y: number, z: number) => ({ x, y, z })),
  ShaderMaterial: vi.fn(),
  MeshBasicMaterial: vi.fn(),
  AmbientLight: vi.fn(),
  DirectionalLight: vi.fn(() => ({ position: { set: vi.fn() } })),
  BackSide: 1,
  AdditiveBlending: 2,
}));

function renderHome() {
  return render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>
  );
}

describe("HomePage production claims", () => {
  it("has no KPI section", () => {
    const { container } = render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );
    expect(container.querySelector(".home-kpis")).toBeNull();
    expect(container.textContent).not.toContain("Production AUC");
    expect(container.textContent).not.toContain("Retrain cycles");
    expect(container.textContent).not.toContain("Tests passed");
  });

  it("step 02 says RandomForest, not Stacked", () => {
    const { container } = render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );
    expect(container.textContent).toContain("RandomForest");
    expect(container.textContent).not.toContain("Stacked");
  });

  it("does not claim 32 countries", () => {
    const { container } = render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );
    expect(container.textContent).not.toContain("32 countries");
  });

  it("does not mention live tile or 300ms", () => {
    const { container } = render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>
    );
    expect(container.textContent).not.toContain("live tile");
    expect(container.textContent).not.toContain("300ms");
  });

  it("headline says ESG screening for sustainability projects", () => {
    const { container } = renderHome();
    // The <br/> adds no text, so the two lines read as one run in textContent.
    expect(container.querySelector("h1")?.textContent).toBe(
      "ESG screening forsustainability projects."
    );
  });

  it("hero says a fixed formula scores the figures and the ML estimate is experimental", () => {
    const { container } = renderHome();
    expect(container.textContent).toContain(
      "A fixed formula scores the figures you enter against country benchmarks. Beside it sits an experimental ML success estimate with SHAP explanations. The model learned from an administrative proxy, not from verified project outcomes; it is checked daily for drift, and a retrained model replaces it only after passing a quality gate."
    );
  });

  it("step 02 says the ESG total is a fixed formula and the probability is unvalidated", () => {
    const { container } = renderHome();
    expect(container.textContent).toContain(
      "A fixed formula returns the ESG total and its env/soc/eco breakdown. A RandomForest adds a success probability, unvalidated; the risk level combines the two."
    );
  });

  it("step 03 says the scores are screening indices, not measured outcomes", () => {
    const { container } = renderHome();
    expect(container.textContent).toContain(
      "Run two projects side by side and see the gap on every axis. The scores are screening indices, not measured outcomes."
    );
  });

  it("drops the claims the product does not back", () => {
    const { container } = renderHome();
    for (const phrase of [
      "Planetary-scale",
      "explainable down to the feature",
      "closed-loop MLOps",
      "AI Teammate",
      "Decide with nrs",
      "A RandomForest model returns ESG total",
    ]) {
      expect(container.textContent).not.toContain(phrase);
    }
  });
});
