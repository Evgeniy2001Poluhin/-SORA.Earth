/**
 * The basemap must not be watermarked, and the world must not repeat.
 *
 * Reported from production on 2026-09-03: every tile on both maps carried
 * "API KEY REQUIRED / carto.com/basemaps/apikey" diagonally across it, and
 * panning east showed North America a second time with markers on neither copy
 * but the first.
 *
 * Two separate causes, so two separate assertions below.
 *
 * CARTO began stamping tiles served without a key. No key was ever configured
 * here — `git log -S cartocdn` shows the URL added without one — so the policy
 * changed under a URL that did not, and nothing in the repository failed.
 *
 * The repetition was `WORLD_BOUNDS = [[-65, -Infinity], [82, Infinity]]`. An
 * infinite longitude bound is not a bound: `maxBounds` was set and
 * `maxBoundsViscosity` was 1.0, and the map still scrolled sideways forever.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { TILE_URL, TILE_ATTRIBUTION, WORLD_BOUNDS } from "./tiles";

const HERE = join(process.cwd(), "src", "features", "map");
const readRaw = (name: string) => readFileSync(join(HERE, name), "utf-8");

/**
 * Source with comments removed.
 *
 * The first version of the last test below failed on its own explanation:
 * RussiaMap.tsx says in a comment *why* it must not use `noWrap`, and a plain
 * substring search found that sentence and called it code. A test about what a
 * component does has to read what it does.
 */
const read = (name: string) =>
  readRaw(name)
    .replace(/\/\*[\s\S]*?\*\//g, " ")   // block comments, JSX ones included
    .replace(/^\s*\/\/.*$/gm, " ");        // line comments

/** Hosts that watermark, meter or refuse tiles without an account. */
const KEYED_PROVIDERS = [
  "cartocdn.com",
  "api.mapbox.com",
  "tiles.stadiamaps.com",
  "api.maptiler.com",
  "thunderforest.com",
];

describe("the basemap", () => {
  it("does not come from a provider that requires a key", () => {
    for (const host of KEYED_PROVIDERS) {
      expect(TILE_URL).not.toContain(host);
    }
  });

  it("carries no key or token in the URL", () => {
    // A key in the URL would be a public credential in a client bundle, which
    // is a different problem from the one being fixed and no better.
    expect(TILE_URL).not.toMatch(/[?&](api_?key|access_?token|key)=/i);
  });

  it("credits the source, because the tile policy requires it", () => {
    expect(TILE_ATTRIBUTION).toMatch(/openstreetmap/i);
  });
});

describe("the world map's bounds", () => {
  it("are finite in longitude", () => {
    const [[, west], [, east]] = WORLD_BOUNDS;
    expect(Number.isFinite(west)).toBe(true);
    expect(Number.isFinite(east)).toBe(true);
  });

  it("stay within one world", () => {
    const [[south, west], [north, east]] = WORLD_BOUNDS;
    expect(west).toBeGreaterThanOrEqual(-180);
    expect(east).toBeLessThanOrEqual(180);
    expect(south).toBeLessThan(north);
  });
});

describe("the map components", () => {
  it("both take their tiles from one place", () => {
    // Two hardcoded URLs is how they came to disagree in the first place: the
    // CARTO address was written out twice, so a provider change is two edits
    // and one of them gets forgotten.
    for (const file of ["MapPage.tsx", "RussiaMap.tsx"]) {
      const body = read(file);
      expect(readRaw(file)).toContain('from "./tiles"');
      expect(body).not.toContain("cartocdn");
      expect(body).not.toMatch(/url="https:\/\//);
    }
  });

  it("stops the world map wrapping", () => {
    // `noWrap` is what actually prevents the repeat; the bounds alone did not.
    expect(read("MapPage.tsx")).toMatch(/\bnoWrap\b/);
  });

  it("does not put noWrap on the Russia map", () => {
    // Deliberate, and the opposite of the line above. Russia's bounds reach
    // longitude 185 because Chukotka lies east of the antimeridian, and Leaflet
    // serves that strip from the wrapped copy — `noWrap` would render the far
    // east as empty grey. It is bounded by `maxBounds` instead.
    const body = read("RussiaMap.tsx");
    expect(body).not.toMatch(/\bnoWrap\b/);
    expect(body).toMatch(/maxBounds=/);
  });
});
