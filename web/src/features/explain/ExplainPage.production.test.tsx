/**
 * ExplainPage against the production path (#218).
 *
 * The mock-mode file asserts that the canned SHAP payload carries the field
 * names the page reads and echoes the caller's inputs. Neither can see the
 * branch that runs on the deployed site, where `explainApi.local` and the
 * waterfall image are two separate requests that can fail independently.
 *
 * What matters most here: an explanation that failed must not leave feature
 * attributions on the page. A SHAP row reading `co2_reduction +8.4` when the
 * server never answered is precisely the #216 failure in a new place.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { ExplainPage } from "./ExplainPage";
import { explainApi } from "@/api/endpoints/explain";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { callsOf, scoreLikeNumbersIn, stubStatus, stubTransportError } from "@/test/http";

const REQUEST = { budget: 150000, co2_reduction: 340, social_impact: 9, duration_months: 18 };

/** Feature names the canned payload does not use, so a row rendered from the
 *  mock instead of from this is visible rather than plausible. */
const SERVER_EXPLANATION = {
  prediction: 0.611,
  base_value: 0.402,
  top_contributions: [
    { feature: "server_side_feature_a", value: 1, shap_value: 0.21 },
    { feature: "server_side_feature_b", value: 2, shap_value: -0.09 },
  ],
};

/** The page fires two requests at once. Answering them by URL keeps the
 *  waterfall an image and the explanation JSON, as in production. */
function stubExplainEndpoints() {
  const stub = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("waterfall")) {
      return new Response("<svg xmlns='http://www.w3.org/2000/svg'/>", {
        status: 200,
        headers: { "content-type": "image/svg+xml" },
      });
    }
    return new Response(JSON.stringify(SERVER_EXPLANATION), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("SHAP explain on the production path", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("asks the explain endpoint rather than answering from canned data", async () => {
    const fetchStub = stubExplainEndpoints();

    await explainApi.local(REQUEST);

    const [call] = callsOf(fetchStub);
    expect(call.url).toBe("/api/v1/explain/local");
    expect(call.method).toBe("POST");
    expect(call.body).toMatchObject({ budget: 150000, co2_reduction: 340 });
  });

  it("returns the server's contributions, unaltered", async () => {
    stubExplainEndpoints();

    const data = await explainApi.local(REQUEST);

    expect(data).toEqual(SERVER_EXPLANATION);
    expect(data.top_contributions.map((c) => c.feature)).toEqual([
      "server_side_feature_a",
      "server_side_feature_b",
    ]);
  });

  it("renders the server's feature names, not the canned ones", async () => {
    stubExplainEndpoints();
    const user = userEvent.setup();
    renderWithQuery(<ExplainPage />);

    await user.click(screen.getByRole("button", { name: "MED" }));

    await waitFor(
      () => expect(screen.getByText("server_side_feature_a")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    // The canned payload's names must not appear when the server answered.
    expect(screen.queryByText("co2_reduction")).not.toBeInTheDocument();
  });

  it("a 500 rejects instead of returning an explanation", async () => {
    stubStatus(500);
    await expect(explainApi.local(REQUEST)).rejects.toThrow(/500/);
  });

  it("a failing waterfall rejects rather than yielding a blank image", async () => {
    stubStatus(500);
    await expect(explainApi.waterfallBlob(REQUEST)).rejects.toThrow(/500/);
  });

  it("a dropped connection rejects instead of returning an explanation", async () => {
    stubTransportError();
    await expect(explainApi.local(REQUEST)).rejects.toThrow();
  });

  it("the rejection carries no number a caller could render", async () => {
    stubStatus(503);

    const error = await explainApi.local(REQUEST).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(Error);
    expect(scoreLikeNumbersIn({ ...(error as Error) })).toEqual([]);
  });

  it("leaves no attribution on the page when the request fails", async () => {
    // The component's half: `onError` shows a toast and never calls setJson,
    // so the empty state must survive a failed explain.
    stubStatus(500);
    const user = userEvent.setup();
    renderWithQuery(<ExplainPage />);

    expect(screen.getByText("No data yet")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "MED" }));

    await waitFor(() => expect(screen.getByText("No data yet")).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.queryByText("server_side_feature_a")).not.toBeInTheDocument();
    expect(screen.queryByText("co2_reduction")).not.toBeInTheDocument();
  });
});
