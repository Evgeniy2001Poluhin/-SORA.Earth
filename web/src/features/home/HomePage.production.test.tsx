/**
 * The home page states only what is true.
 *
 * Finding 12 noted several false claims: six literal KPI tiles, "Stacked ML
 * model" in step 02 (it is a RandomForest), "32 countries" in the hero and
 * tour (27 in the evaluation list, 33 on the map), and "Same backend that
 * powers the live tile above. ~300ms per evaluation." (no live tile, 300ms
 * never measured). The page's claims are checked on the rendered page. The
 * tour's step text is not covered by a test, because the tour renders into a
 * portal and shows one step at a time, and exporting its steps only for a test
 * would change production code.
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
});
