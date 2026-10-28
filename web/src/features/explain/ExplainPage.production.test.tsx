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
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { ExplainPage } from "./ExplainPage";
import { explainApi } from "@/api/endpoints/explain";
import { isMock } from "@/api/mock";
import { ANALYST, deferred, renderWithQuery, seedSession } from "@/test/utils";
import { useAuth } from "@/store/auth";
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
function stubExplainEndpoints(payload: unknown = SERVER_EXPLANATION) {
  const stub = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("waterfall")) {
      return new Response("<svg xmlns='http://www.w3.org/2000/svg'/>", {
        status: 200,
        headers: { "content-type": "image/svg+xml" },
      });
    }
    return new Response(JSON.stringify(payload), {
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

/** The value cell of one contribution row, as the reader sees it. */
const valueFor = (feature: string) =>
  screen.getByText(feature).closest(".contrib-row")?.querySelector(".contrib-val")?.textContent;

describe("ExplainPage: what the numbers and labels claim", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // jsdom has no object-URL support, and the page needs it to show the image.
    Object.defineProperty(URL, "createObjectURL", { configurable: true, writable: true, value: vi.fn(() => "blob:test") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, writable: true, value: vi.fn() });
  });

  // F025: the subtitle's fixed latency figures are not measured on anything shown.
  it("quotes no fixed latency figures and no cache behaviour in the subtitle", () => {
    const { container } = renderWithQuery(<ExplainPage />);
    expect(container.textContent).not.toMatch(/\b500 ?ms\b/);
    expect(container.textContent).not.toMatch(/\b14 ?ms\b/);
    expect(container.textContent).not.toContain("disk-cached");
  });

  // F025/F026: the response carries no cache field or header, so no cache verdict
  // may be shown, however fast the call was.
  it("shows the measured time after a request, with no cached or cold verdict", async () => {
    stubExplainEndpoints();
    const user = userEvent.setup();
    const { container } = renderWithQuery(<ExplainPage />);

    await user.click(screen.getByRole("button", { name: "MED" }));

    await waitFor(() => expect(container.querySelector(".elapsed-pill")).not.toBeNull(), { timeout: 3000 });
    expect(container.querySelector(".elapsed-pill")!.textContent).toMatch(/^\d+ ?ms$/);
    expect(container.textContent).not.toContain("cached");
    expect(container.textContent).not.toContain("cold");
  });

  // F027: a SHAP value the server did not send is unknown, not zero; a real zero is zero.
  it("shows a missing SHAP value as a dash, never as 0.000, and keeps a real 0", async () => {
    stubExplainEndpoints({
      prediction: 0.611,
      base_value: 0.402,
      top_contributions: [
        { feature: "feature_null", value: 1, shap_value: null },
        { feature: "feature_absent", value: 2 },
        { feature: "feature_zero", value: 3, shap_value: 0 },
        { feature: "feature_positive", value: 4, shap_value: 0.21 },
      ],
    });
    const user = userEvent.setup();
    renderWithQuery(<ExplainPage />);

    await user.click(screen.getByRole("button", { name: "MED" }));

    await waitFor(() => expect(screen.getByText("feature_positive")).toBeInTheDocument(), { timeout: 3000 });
    expect(valueFor("feature_null")).toBe("—");
    expect(valueFor("feature_absent")).toBe("—");
    expect(valueFor("feature_zero")).toBe("0.000");
    expect(valueFor("feature_positive")).toBe("+0.210");
  });
});

describe("the waterfall belongs to the session that asked for it", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    seedSession(ANALYST, "tok-ana");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, writable: true, value: vi.fn(() => "blob:test") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, writable: true, value: vi.fn() });
  });

  it("carries the session's Bearer, so a signed-in waterfall stays out of the shared PNG cache", async () => {
    const stub = stubExplainEndpoints();
    renderWithQuery(<ExplainPage />);
    await userEvent.click(screen.getByRole("button", { name: "MED" }));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));

    const waterfall = callsOf(stub).find((c) => c.url === "/api/v1/predict/explain/waterfall")!;
    expect(waterfall.headers["Authorization"]).toBe("Bearer tok-ana");
  });

  it("makes no image, and shows no result, for an answer that arrives after a sign-out", async () => {
    const image = deferred<Response>();
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes("waterfall")
        ? image.promise
        : new Response(JSON.stringify(SERVER_EXPLANATION), { status: 200, headers: { "content-type": "application/json" } }),
    ) as unknown as typeof fetch;
    renderWithQuery(<ExplainPage />);
    await userEvent.click(screen.getByRole("button", { name: "MED" }));

    await act(async () => { await useAuth.getState().logout(); });
    await act(async () => {
      image.resolve(new Response("<svg xmlns='http://www.w3.org/2000/svg'/>", { status: 200, headers: { "content-type": "image/svg+xml" } }));
      await new Promise((r) => setTimeout(r, 30));
    });

    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(screen.queryByAltText("SHAP waterfall")).toBeNull();
    expect(screen.queryByText("server_side_feature_a")).toBeNull();
  });

  it("revokes its object URL when the page goes", async () => {
    stubExplainEndpoints();
    const { unmount } = renderWithQuery(<ExplainPage />);
    await userEvent.click(screen.getByRole("button", { name: "MED" }));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));

    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test");
  });
});
