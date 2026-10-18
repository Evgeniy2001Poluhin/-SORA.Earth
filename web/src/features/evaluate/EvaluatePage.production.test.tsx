/**
 * EvaluatePage against a successful-but-empty answer (#236).
 *
 * Found while writing the production-mode coverage for #218: a 200 carrying
 * `{}` passed the `!result` guard (an empty object is truthy), reached
 * `fmtNum(result.budget_usd)` with `undefined`, and took the entire page
 * down — `Cannot read properties of undefined (reading 'toLocaleString')`,
 * a blank white screen where the ESG score belongs.
 *
 * That is the loudest face of the same defect `UncertaintyCard` (0.0%) and
 * `WhatIf` (+0.00 bars) showed quietly. All three come from one missing
 * distinction: **absent** and **empty** are different answers.
 *
 * The formatter in `src/lib/format.ts` is deliberately left strict rather
 * than taught to print a dash for `undefined`. A crash is loud; a dash where
 * a score belongs is the quiet kind of wrong (#216). The fix is to stop
 * calling it with nothing.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { EvaluatePage } from "./EvaluatePage";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { stubJson, stubStatus } from "@/test/http";
import { SUCCESS_PROBABILITY_LABEL, SUCCESS_PROBABILITY_NOTE } from "./successProbabilityNote";

const renderPage = () => renderWithQuery(<MemoryRouter><EvaluatePage /></MemoryRouter>);
const runEvaluation = async () => {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Run evaluation" }));
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("EvaluatePage when the API answers with nothing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps the page standing instead of blanking it", async () => {
    stubJson({});

    const { container } = renderPage();
    await runEvaluation();
    await new Promise((resolve) => setTimeout(resolve, 500));

    // The regression this file exists for: the whole page used to unmount.
    expect(container.textContent ?? "").not.toBe("");
    expect(container.querySelector(".ev-result")).not.toBeNull();
  });

  it("shows the empty state rather than a score of zero", async () => {
    stubJson({});

    renderPage();
    await runEvaluation();

    await waitFor(() => expect(screen.getByText(/No evaluation yet/i)).toBeInTheDocument(), {
      timeout: 3000,
    });
  });

  it("shows the empty state when the request fails outright", async () => {
    stubStatus(500);

    renderPage();
    await runEvaluation();
    await new Promise((resolve) => setTimeout(resolve, 500));

    expect(screen.getByText(/No evaluation yet/i)).toBeInTheDocument();
  });

  it("renders the score when the server actually sends one", async () => {
    // The other half: the guard must not suppress a real answer. 61.5 is
    // unlike anything the canned payload produces.
    stubJson({
      total_score: 61.5,
      environment_score: 55.5,
      social_score: 70.5,
      economic_score: 58.5,
      risk_level: "Medium",
      success_probability: 66.5,
      recommendations: [],
      region: "EU",
    });

    renderPage();
    await runEvaluation();

    await waitFor(() => expect(screen.queryByText(/No evaluation yet/i)).not.toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByText("Medium risk")).toBeInTheDocument();
  });
});

describe("PDF download escaping (GHSA-jmpv-7wjf-87q9)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("escapes HTML in project names and country names", async () => {
    interface MockWindow {
      document: {
        write: ReturnType<typeof vi.fn>;
        close: ReturnType<typeof vi.fn>;
      };
      print: ReturnType<typeof vi.fn>;
      opener: null | undefined;
    }

    const mockWindow: MockWindow = {
      document: {
        write: vi.fn(),
        close: vi.fn(),
      },
      print: vi.fn(),
      opener: undefined,
    };
    const openSpy = vi.spyOn(window, "open").mockReturnValue(mockWindow as unknown as Window);

    stubJson({
      total_score: 75.0,
      environment_score: 70.0,
      social_score: 80.0,
      economic_score: 75.0,
      risk_level: "Low",
      success_probability: 80.0,
      recommendations: ["Install solar panels"],
      region: "Europe",
    });

    const { container } = renderPage();

    // Fill form with markup in the project name. The input has no accessible name/id,
    // so we query by the label text and find the input inside the field.
    const user = userEvent.setup();
    const fieldLabels = Array.from(container.querySelectorAll(".ev-field label"));
    const projectNameField = fieldLabels.find((l) => l.textContent === "Project name")?.parentElement;
    const nameInput = projectNameField?.querySelector("input");
    if (!nameInput) throw new Error("Project name input not found");
    await user.clear(nameInput);
    await user.type(nameInput, '<b id="x">x</b>');

    await runEvaluation();
    await waitFor(() => expect(screen.queryByText(/No evaluation yet/i)).not.toBeInTheDocument(), {
      timeout: 3000,
    });

    // Click Download PDF
    const pdfButton = screen.getByText(/Download PDF/i);
    await user.click(pdfButton);

    // Check that window.open was called and document.write was called with escaped HTML
    expect(openSpy).toHaveBeenCalled();
    expect(mockWindow.document.write).toHaveBeenCalled();
    const writtenHtml = mockWindow.document.write.mock.calls[0][0];

    // The markup should be escaped
    expect(writtenHtml).toContain("&lt;b id=&quot;x&quot;&gt;x&lt;/b&gt;");
    expect(writtenHtml).not.toContain("<b id=\"x\">x</b>");

    // And opener should be nulled
    expect(mockWindow.opener).toBeNull();

    openSpy.mockRestore();
  });
});

describe("the success probability is labelled as an unvalidated estimate", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const answer = {
    total_score: 61.5,
    environment_score: 55.5,
    social_score: 70.5,
    economic_score: 58.5,
    risk_level: "Medium",
    success_probability: 66.5,
    recommendations: [],
    region: "EU",
  };

  it("shows the label, the note, and the hover text beside the number", async () => {
    stubJson(answer);

    const { container } = renderPage();
    await runEvaluation();

    await waitFor(() => expect(screen.getByText(SUCCESS_PROBABILITY_LABEL)).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByText(SUCCESS_PROBABILITY_NOTE)).toBeInTheDocument();
    expect(container.querySelector(".ev-prob")).toHaveAttribute("title", SUCCESS_PROBABILITY_NOTE);
  });

  it("says the same in the printed report", async () => {
    const mockWindow = {
      document: { write: vi.fn(), close: vi.fn() },
      print: vi.fn(),
      opener: undefined,
    };
    const openSpy = vi.spyOn(window, "open").mockReturnValue(mockWindow as unknown as Window);
    stubJson(answer);

    renderPage();
    await runEvaluation();
    await waitFor(() => expect(screen.getByText(SUCCESS_PROBABILITY_LABEL)).toBeInTheDocument(), {
      timeout: 3000,
    });
    await userEvent.setup().click(screen.getByText(/Download PDF/i));

    expect(openSpy).toHaveBeenCalled();
    const writtenHtml = mockWindow.document.write.mock.calls[0][0];
    expect(writtenHtml).toContain("66.5% success probability (unvalidated model estimate)");

    openSpy.mockRestore();
  });
});

/* -- the hero's Models and Status come from GET /api/v1/health, and nothing else -- */

const HEALTH_URL = "/api/v1/health";
const jsonResponse = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });

const HEALTHY = {
  status: "healthy",
  model_provenance: { source: "seed", model_version: "20260712_200332" },
  checks: {
    models: { status: "healthy", loaded: true, neural_network_loaded: false, xgboost_loaded: true },
  },
};

/**
 * Answers GET /api/v1/health with `answer()` and every other call with
 * `otherAnswer` (an empty object by default), so the hero is judged on the health
 * answer alone. A fresh Response per call, since a body can be read once. Same
 * transport shape as stubJson and stubStatus in @/test/http, routed by URL.
 */
function stubHealth(answer: () => Response, otherAnswer: unknown = {}) {
  const stub = vi.fn(async (input: RequestInfo | URL) =>
    String(input) === HEALTH_URL ? answer() : jsonResponse(otherAnswer),
  );
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}

/** The whole hero, which must exist: a missing hero would make a negative match pass. */
function heroText(container: HTMLElement): string {
  const hero = container.querySelector(".ev-hero");
  if (!hero) throw new Error("no .ev-hero on the page");
  return hero.textContent ?? "";
}

/** The value beside a hero label ("Models", "Status"), read from the DOM. */
function heroValue(container: HTMLElement, label: string): string {
  const row = Array.from(container.querySelectorAll(".ev-hero-row")).find(
    (r) => r.querySelector(".k")?.textContent === label,
  );
  if (!row) throw new Error(`no "${label}" row in the hero`);
  return row.querySelector(".v")?.textContent ?? "";
}

/**
 * Waits until the health call has been made and has settled. Waiting for the
 * request alone is not enough: "Unknown" is also what the hero shows before any
 * answer arrives, so a check made too early would pass for the wrong reason.
 */
async function settleHealth(stub: ReturnType<typeof stubHealth>, queryClient: QueryClient) {
  await waitFor(() => expect(stub).toHaveBeenCalledWith(HEALTH_URL, expect.anything()));
  await waitFor(() => expect(queryClient.isFetching()).toBe(0));
}

describe("the hero reports the health answer, and nothing it was not given", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("names the loaded models and says Operational for a healthy service", async () => {
    // The neural network is not loaded in production, so MLP must not appear.
    const stub = stubHealth(() => jsonResponse(HEALTHY));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroValue(container, "Models")).toBe("RandomForest · XGBoost");
    expect(heroValue(container, "Status")).toBe("Operational");
  });

  it("names MLP only when the service reports the network loaded", async () => {
    const stub = stubHealth(() =>
      jsonResponse({
        ...HEALTHY,
        checks: { models: { loaded: true, xgboost_loaded: true, neural_network_loaded: true } },
      }),
    );
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroValue(container, "Models")).toBe("RandomForest · XGBoost · MLP");
  });

  it("shows unknown, not Operational and not a default list, for an empty 200", async () => {
    const stub = stubHealth(() => jsonResponse({}));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroValue(container, "Models")).toBe("unknown");
    expect(heroValue(container, "Status")).toBe("Unknown");
    expect(heroText(container)).not.toMatch(/MLP|Operational/);
  });

  it("shows unknown, not a default list, when the models block reports no flags", async () => {
    // The models object is present, but says nothing about which models are loaded.
    const stub = stubHealth(() =>
      jsonResponse({ status: "healthy", checks: { models: { status: "healthy" } } }),
    );
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroValue(container, "Models")).toBe("unknown");
    expect(heroValue(container, "Status")).toBe("Operational");
    expect(heroText(container)).not.toMatch(/MLP|RandomForest|XGBoost/);
  });

  it("shows unknown when the health request fails", async () => {
    const stub = stubHealth(() => new Response("upstream said no", { status: 503 }));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroValue(container, "Models")).toBe("unknown");
    expect(heroValue(container, "Status")).toBe("Unknown");
    expect(heroText(container)).not.toMatch(/MLP|Operational/);
  });

  it("shows Degraded for a status other than healthy", async () => {
    const stub = stubHealth(() => jsonResponse({ ...HEALTHY, status: "degraded" }));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroValue(container, "Status")).toBe("Degraded");
    expect(heroText(container)).not.toMatch(/Operational/);
  });

  it("has no AUC and none of the invented figures in the hero", async () => {
    const stub = stubHealth(() => jsonResponse(HEALTHY));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroText(container)).not.toMatch(/AUC/);
    expect(heroText(container)).not.toMatch(/0\.82|0\.98/);
  });
});

/* -- the printed report names the models the service reports, not a fixed list -- */

const SCORED = {
  total_score: 61.5,
  environment_score: 55.5,
  social_score: 70.5,
  economic_score: 58.5,
  risk_level: "Medium",
  success_probability: 66.5,
  recommendations: [],
  region: "EU",
};

/**
 * Runs an evaluation with the given health answer, presses Download PDF, and
 * returns the HTML the page wrote to the print window. Same mechanism as the PDF
 * escaping test above. `afterFirstAnswer`, when given, runs once the first health
 * answer has settled, to change what the service answers next.
 */
async function printedReportWith(
  health: () => Response,
  afterFirstAnswer?: (queryClient: QueryClient) => Promise<void>,
): Promise<string> {
  const mockWindow = {
    document: { write: vi.fn(), close: vi.fn() },
    print: vi.fn(),
    opener: undefined,
  };
  const openSpy = vi.spyOn(window, "open").mockReturnValue(mockWindow as unknown as Window);
  const stub = stubHealth(health, SCORED);

  const { queryClient } = renderPage();
  await settleHealth(stub, queryClient);
  if (afterFirstAnswer) await afterFirstAnswer(queryClient);
  await runEvaluation();
  await waitFor(() => expect(screen.getByText("Medium risk")).toBeInTheDocument(), {
    timeout: 3000,
  });
  await userEvent.setup().click(screen.getByText(/Download PDF/i));

  expect(openSpy).toHaveBeenCalled();
  openSpy.mockRestore();
  return mockWindow.document.write.mock.calls[0][0] as string;
}

describe("the printed report names the models the service reports", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("names the loaded models, and not MLP, when the network is not loaded", async () => {
    const html = await printedReportWith(() => jsonResponse(HEALTHY));

    expect(html).toContain("models reported by the service at export: RandomForest · XGBoost");
    expect(html).not.toContain("MLP");
  });

  it("says unknown when the health request fails", async () => {
    const html = await printedReportWith(() => new Response("upstream said no", { status: 503 }));

    expect(html).toContain("models reported by the service at export: unknown");
    expect(html).not.toContain("MLP");
  });

  it("says unknown after a refetch fails, even though the last good answer is still cached", async () => {
    let answer: () => Response = () => jsonResponse(HEALTHY);
    const html = await printedReportWith(
      () => answer(),
      async (queryClient) => {
        answer = () => new Response("upstream said no", { status: 503 });
        await queryClient.invalidateQueries({ queryKey: ["service-health"] });
        await waitFor(() => expect(queryClient.getQueryState(["service-health"])?.status).toBe("error"));
      },
    );

    expect(html).toContain("models reported by the service at export: unknown");
    expect(html).not.toContain("RandomForest");
    expect(html).not.toContain("MLP");
  });
});

/* -- a failed refetch is not answered with the last good answer -- */

describe("a failed health refetch does not keep the last good answer", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("reads Unknown after the refetch fails, and Operational again once it recovers", async () => {
    let answer: () => Response = () => jsonResponse(HEALTHY);
    const stub = stubHealth(() => answer());
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroValue(container, "Status")).toBe("Operational");
    expect(heroValue(container, "Models")).toBe("RandomForest · XGBoost");

    // The service stops answering. The last good answer is still in the cache.
    answer = () => new Response("upstream said no", { status: 503 });
    await queryClient.invalidateQueries({ queryKey: ["service-health"] });
    await waitFor(() => expect(queryClient.getQueryState(["service-health"])?.status).toBe("error"));

    expect(heroValue(container, "Status")).toBe("Unknown");
    expect(heroValue(container, "Models")).toBe("unknown");
    expect(heroText(container)).not.toMatch(/Operational|RandomForest|XGBoost|MLP/);

    // The service answers healthy again, and a later successful answer is trusted.
    answer = () => jsonResponse(HEALTHY);
    await queryClient.invalidateQueries({ queryKey: ["service-health"] });
    await waitFor(() => expect(heroValue(container, "Status")).toBe("Operational"));
    expect(heroValue(container, "Models")).toBe("RandomForest · XGBoost");
  });
});

/* -- a partial models block names what it states, and never claims none loaded for what it does not -- */

describe("a partial models block is not reported as none loaded", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    ["only loaded, false", { loaded: false }, "unknown"],
    ["only xgboost_loaded, false", { xgboost_loaded: false }, "unknown"],
    ["only neural_network_loaded, false", { neural_network_loaded: false }, "unknown"],
    ["loaded true, the other two missing", { loaded: true }, "RandomForest · others unknown"],
    ["only xgboost_loaded, true", { xgboost_loaded: true }, "XGBoost · others unknown"],
    ["only neural_network_loaded, true", { neural_network_loaded: true }, "MLP · others unknown"],
    [
      "loaded true, xgboost null, neural false",
      { loaded: true, xgboost_loaded: null, neural_network_loaded: false },
      "RandomForest · others unknown",
    ],
    ["loaded as the string \"true\"", { loaded: "true" }, "unknown"],
    [
      "all three false",
      { loaded: false, xgboost_loaded: false, neural_network_loaded: false },
      "none loaded",
    ],
    [
      "all three true",
      { loaded: true, xgboost_loaded: true, neural_network_loaded: true },
      "RandomForest · XGBoost · MLP",
    ],
  ])("when the models block says %s", async (_case, models, expected) => {
    const stub = stubHealth(() => jsonResponse({ status: "healthy", checks: { models } }));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroValue(container, "Models")).toBe(expected);
  });
});

/* -- an empty status is unknown, not degraded -- */

describe("an empty status is unknown, not degraded", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it.each([[""], ["  "]])("reads Unknown for the status %j", async (status) => {
    const stub = stubHealth(() => jsonResponse({ ...HEALTHY, status }));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroValue(container, "Status")).toBe("Unknown");
    expect(heroText(container)).not.toMatch(/Degraded|Operational/);
  });
});

/* -- the hero says what the score is, and nothing it cannot back (audit F006-F008) -- */

// <br/> adds no text, so the two halves of the headline meet in textContent.
const HERO_HEADLINE = "Evaluate an ESG projectfrom the figures you enter.";
const HERO_PARAGRAPH =
  "A fixed formula scores environment, social and economic factors against country benchmarks. An experimental ML estimate with SHAP explanations sits beside it; it was learned from an administrative proxy, not verified outcomes.";
const DROPPED_CLAIMS = [
  "Calibrated ESG Models",
  "planetary precision",
  "closed-loop retraining",
  "ML Scoring Engine",
];
const UNIT_TEXT = "Formula score + experimental ML estimate";

describe("the Evaluate hero says what the score is", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("states the headline and the paragraph exactly", async () => {
    const stub = stubHealth(() => jsonResponse(HEALTHY));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    const heading = container.querySelector(".ev-hero h1");
    expect(heading?.textContent).toBe(HERO_HEADLINE);
    expect(heading?.querySelector("em")?.textContent).toBe("ESG");
    expect(screen.getByText(HERO_PARAGRAPH)).toBeInTheDocument();
  });

  it("labels the eyebrow as a formula with an experimental estimate, not calibration", async () => {
    const stub = stubHealth(() => jsonResponse(HEALTHY));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroText(container)).toContain("Formula score · Experimental ML estimate");
  });

  it("names the Unit as the formula plus the estimate, not an ML engine", async () => {
    const stub = stubHealth(() => jsonResponse(HEALTHY));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    expect(heroValue(container, "Unit")).toBe(`◆ ${UNIT_TEXT}`);
    expect(container.textContent ?? "").not.toContain("ML Scoring Engine");
  });

  it("does not call the printed report footer an ML scoring engine", async () => {
    const html = await printedReportWith(() => jsonResponse(HEALTHY));

    expect(html).toContain(`· ${UNIT_TEXT} ·`);
    expect(html).not.toContain("ML Scoring Engine");
  });

  it("drops the claims the data does not back, anywhere on the page", async () => {
    const stub = stubHealth(() => jsonResponse(HEALTHY));
    const { container, queryClient } = renderPage();
    await settleHealth(stub, queryClient);

    for (const claim of DROPPED_CLAIMS) {
      expect(heroText(container)).not.toContain(claim);
      expect(container.textContent ?? "").not.toContain(claim);
    }
  });
});
