/**
 * The header badge must not paint a model identity the server did not send.
 *
 * `/api/v2/model/version` answered 200 with `"version": "None"` when no bundle
 * was loaded, and the badge drew it as "@ champion vNone" on every page. The
 * backend now answers 503 for that (tests/test_ml_v2_no_registered_model.py),
 * which lands in the error branch below. This file covers the frontend half:
 * an answer that does arrive without an alias or a version must not show as
 * "production", a "-" placeholder, or a retrain date. Audit finding F077.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { ModelBadge } from "./ModelBadge";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";

/** Routes the two requests `fetchModelVersion` makes. The v1 status call is
 *  best-effort enrichment; a test that needs `meta.retrained_at` passes it. */
function stubModel(versionStatus: number, versionBody: unknown, statusBody: unknown = {}) {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const isVersion = String(input).includes("/api/v2/model/version");
    return new Response(JSON.stringify(isVersion ? versionBody : statusBody), {
      status: isVersion ? versionStatus : 200,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

/** The badge element, once the query has resolved past its "model…" state. */
async function settledBadge(container: HTMLElement): Promise<HTMLElement> {
  await waitFor(() => expect(container.textContent).not.toContain("model…"));
  const el = container.querySelector<HTMLElement>(".model-badge");
  if (!el) throw new Error("no badge rendered");
  return el;
}

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("ModelBadge when the server sends no alias", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows unknown, never production", async () => {
    stubModel(200, { name: "esg-success-predictor", version: "3f9a1c2e7b8d" });
    const { container } = renderWithQuery(<ModelBadge />);
    const badge = await settledBadge(container);

    expect(badge.textContent).toBe("@unknown·v3f9a1c2e");
    expect(badge.className).toContain("model-badge--unknown");
    expect(badge.textContent).not.toContain("production");
    expect(badge.getAttribute("title")).toBe("esg-success-predictor v3f9a1c2e7b8d @unknown");
  });
});

describe("ModelBadge when the server sends no version", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows unknown, not a placeholder or the retrain date", async () => {
    stubModel(
      200,
      { name: "esg-success-predictor", alias: "champion" },
      { meta: { retrained_at: "2026-10-11T03:00:00" } },
    );
    const { container } = renderWithQuery(<ModelBadge />);
    const badge = await settledBadge(container);

    expect(badge.textContent).toBe("@champion·unknown");
    expect(badge.textContent).not.toContain("2026");
    expect(badge.textContent).not.toContain("v-");
    expect(badge.getAttribute("title")).toBe("esg-success-predictor version unknown @champion");
  });
});

describe("ModelBadge when the server sends empty strings", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows unknown for both an empty alias and an empty version", async () => {
    stubModel(200, { name: "esg-success-predictor", alias: "", version: "" });
    const { container } = renderWithQuery(<ModelBadge />);
    const badge = await settledBadge(container);

    expect(badge.textContent).toBe("@unknown·unknown");
    expect(badge.textContent).not.toContain("production");
    expect(badge.getAttribute("title")).toBe("esg-success-predictor version unknown @unknown");
  });
});

describe("ModelBadge when the server sends a real identity", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the alias and the first eight characters of the version, as before", async () => {
    stubModel(200, {
      name: "esg-success-predictor",
      alias: "champion",
      version: "3f9a1c2e7b8d",
    });
    const { container } = renderWithQuery(<ModelBadge />);
    const badge = await settledBadge(container);

    expect(badge.textContent).toBe("@champion·v3f9a1c2e");
    expect(badge.className).toContain("model-badge--champion");
    expect(badge.getAttribute("title")).toBe("esg-success-predictor v3f9a1c2e7b8d @champion");
  });
});

describe("ModelBadge when the backend has no model loaded", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the error branch and does not show a version", async () => {
    stubModel(503, { detail: "no model bundle loaded" });
    const { container } = renderWithQuery(<ModelBadge />);
    await waitFor(() => expect(container.textContent).toContain("model: n/a"));

    expect(container.textContent).not.toContain("@");
    expect(container.textContent).not.toContain("None");
  });
});

describe("ModelBadge stylesheet", () => {
  it("gives the unknown badge the muted style rather than the default pill", () => {
    // Read from disk: vitest runs with `css: false`, which turns a `?raw` import
    // of a stylesheet into an empty string and makes this assertion vacuous.
    const modelBadgeCss = readFileSync(
      join(process.cwd(), "src", "components", "ModelBadge.css"),
      "utf-8",
    );
    const rule = modelBadgeCss.match(/\.model-badge--unknown\s*\{([^}]*)\}/);
    expect(rule, "no .model-badge--unknown rule in ModelBadge.css").not.toBeNull();
    expect(rule![1]).toContain("var(--muted)");
  });
});
