/**
 * CopilotPage against the production path (#218).
 *
 * Every copilot request is answered here by URL, so each test asserts what the
 * page sends and what it shows for that answer:
 *
 * - the SHAP values in a copilot request are the ones /predict/explain returned
 *   for the form's inputs, and a failed explain sends nothing to the copilot
 *   endpoints (F016, F017);
 * - a restored chat keeps no probability or model version, so it shows "unknown"
 *   (F018, F019, F020, F021);
 * - a real zero is shown as zero (F022);
 * - a retrieval score is a similarity, and the sentinel is a heuristic screen
 *   (F023, F024);
 * - a sessions response without a list is an error, not an empty history (F078).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { CopilotPage } from "./CopilotPage";
import { renderWithQuery } from "@/test/utils";
import { callsOf } from "@/test/http";

// The first test in this file pays one-time render costs (about 4.5 s measured on
// its own), which is too close to vitest's 5 s default once files run in parallel.
vi.setConfig({ testTimeout: 15_000 });

const json = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });

const sse = (events: unknown[]) =>
  new Response(events.map((e) => "data: " + JSON.stringify(e) + "\n\n").join(""), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });

/** Distinct from the constants the page used to send (0.12, 0.08, -0.04). */
const SHAP_FROM_EXPLAIN = [
  { feature: "co2_reduction", value: 8000, shap_value: 0.31, direction: "positive", impact: "high" },
  { feature: "budget", value: 2000000, shap_value: -0.07, direction: "negative", impact: "medium" },
];
const SHAP_SENT = [
  { feature: "co2_reduction", shap_value: 0.31 },
  { feature: "budget", shap_value: -0.07 },
];

const RECOMMENDATION = "Recommended for funding with additional review.";

const CLEAN_SCREEN = {
  passed: true,
  pii_findings: [],
  bias_findings: [],
  policy_violations: [],
  redacted_text: RECOMMENDATION,
  risk_score: 0,
  engine: "regex-v1",
};

const EXPLAIN_ANSWER = { prediction: 1, probability: 87, base_value: 0.4, explanation: SHAP_FROM_EXPLAIN, all_features: [] };

/** A non-streamed copilot answer. Its model_version is the backend's own default. */
const ANSWER = {
  verdict: { label: "High success probability", level: "high" },
  probability: 0.87,
  confidence: "high",
  key_drivers: {
    positive: [{ feature: "co2_reduction", feature_label: "CO2 reduction", shap_value: 0.31 }],
    negative: [{ feature: "budget", feature_label: "budget", shap_value: -0.07 }],
  },
  risks: [],
  recommendation: RECOMMENDATION,
  model_version: "v5",
  explanation_mode: "smart_template",
  sources: [],
  compliance: CLEAN_SCREEN,
  session_id: "sess-1",
};

/** The SSE stream carries no model version in its meta or its done event. */
const STREAM_EVENTS = [
  { type: "meta", probability: 0.87, rag_query: "CO2 reduction 8000 t/yr", session_id: "sess-2" },
  { type: "token", value: "Recommended for funding." },
  { type: "done", sources: [], compliance: CLEAN_SCREEN },
];

const source = (score: number) => ({
  id: "doc-1",
  title: "Verified carbon accounting",
  category: "case_study",
  score,
  excerpt: "A worked example.",
});

/** The explain endpoint as the backend answers it: social_impact must be 0-10
 *  (app/schemas.py, le=10), and anything above is a 422. */
function explainLikeBackend(init?: RequestInit): Response {
  const sent = JSON.parse(String(init?.body ?? "{}")) as { social_impact_score: number };
  if (!(sent.social_impact_score >= 0 && sent.social_impact_score <= 10)) {
    return json({ detail: [{ loc: ["body", "social_impact_score"], msg: "Input should be less than or equal to 10" }] }, 422);
  }
  return json(EXPLAIN_ANSWER);
}

/** A stream that sends a little and never finishes. Like a browser fetch, it errors
 *  its body with AbortError when the request's signal aborts. */
function hangingStream(init?: RequestInit): Response {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
      c.enqueue(encoder.encode('data: {"type":"meta","probability":0.87,"rag_query":"q","session_id":"sess-3"}\n\n'));
      c.enqueue(encoder.encode('data: {"type":"token","value":"Recommended for "}\n\n'));
    },
  });
  init?.signal?.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")));
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

type Route = (init?: RequestInit) => Response;
interface Routes {
  explain?: Route;
  copilot?: Route;
  stream?: Route;
  sessions?: Route;
  session?: Route;
}

/** Answers every request the page makes. Anything else fails the test. */
function backend(routes: Routes = {}) {
  const stub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/predict/explain")) return (routes.explain ?? explainLikeBackend)(init);
    if (url.endsWith("/copilot/explain/stream")) return (routes.stream ?? (() => sse(STREAM_EVENTS)))(init);
    if (url.endsWith("/copilot/explain")) return (routes.copilot ?? (() => json(ANSWER)))(init);
    if (url.endsWith("/copilot/sessions")) return (routes.sessions ?? (() => json({ sessions: [] })))(init);
    if (/\/copilot\/sessions\/[^/]+$/.test(url)) return (routes.session ?? (() => json({ detail: "not found" }, 404)))(init);
    throw new Error("unexpected request in test: " + url);
  });
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}

const renderPage = () => renderWithQuery(<CopilotPage />);

async function generate() {
  await userEvent.click(screen.getByRole("button", { name: "Generate AI Explanation" }));
}

async function streamOnce() {
  await userEvent.click(screen.getByLabelText(/Stream mode/));
  await userEvent.click(screen.getByRole("button", { name: /Stream AI Explanation/ }));
}

const callsTo = (stub: ReturnType<typeof backend>, suffix: string) =>
  callsOf(stub).filter((c) => c.url.endsWith(suffix));

const shapOf = (call: { body: unknown }) => (call.body as { shap_values: unknown }).shap_values;

describe("CopilotPage (production path)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    try { localStorage.clear(); } catch { /* not fatal */ }
  });

  // F016: the normal request carries the attributions the explain endpoint returned.
  it("sends the attributions /predict/explain returned, never the old constants (F016)", async () => {
    const stub = backend();
    renderPage();
    await generate();
    await screen.findByText(RECOMMENDATION);

    expect(callsTo(stub, "/predict/explain")[0].body).toEqual({
      budget_usd: 2000000,
      co2_reduction_tons_per_year: 8000,
      social_impact_score: 8.5,
      project_duration_months: 18,
    });
    expect(shapOf(callsTo(stub, "/copilot/explain")[0])).toEqual(SHAP_SENT);
  });

  // F017: the streaming request carries the same verified attributions.
  it("streams with the same attributions, never the old constants (F017)", async () => {
    const stub = backend();
    renderPage();
    await streamOnce();
    await screen.findByText("Recommended for funding.");

    expect(shapOf(callsTo(stub, "/copilot/explain/stream")[0])).toEqual(SHAP_SENT);
  });

  // F016: a failed explain means no copilot call, and an error the user can read.
  it("sends no copilot request when the explain call fails, and says why (F016)", async () => {
    const stub = backend({
      explain: () => json({ detail: [{ msg: "Input should be less than or equal to 10" }] }, 422),
    });
    renderPage();
    await generate();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No explanation was generated");
    expect(alert).toHaveTextContent("No SHAP values came back");
    expect(callsTo(stub, "/copilot/explain")).toHaveLength(0);
  });

  // F017: the same stop applies to the streaming path.
  it("starts no stream when the explain call fails (F017)", async () => {
    const stub = backend({ explain: () => json({ detail: "boom" }, 500) });
    renderPage();
    await streamOnce();

    expect(await screen.findByRole("alert")).toHaveTextContent("No explanation was generated");
    expect(callsTo(stub, "/copilot/explain/stream")).toHaveLength(0);
  });

  // F018 and F019: a saved chat has no stored probability or model, so neither is shown.
  it("shows a restored chat's probability and model as unknown, not 0 or v5 (F018, F019)", async () => {
    backend({
      sessions: () => json({ sessions: [{ id: "sess-9", title: "Solar farm", created_at: 1, updated_at: Date.now() }] }),
      session: () => json({
        id: "sess-9",
        title: "Solar farm",
        created_at: 1,
        updated_at: 2,
        messages: [
          { id: "m1", role: "user", content: "Explain p=0.87 co2=8000", citations: [], created_at: 1 },
          { id: "m2", role: "assistant", content: RECOMMENDATION, citations: [], created_at: 2 },
        ],
      }),
    });
    const { container } = renderPage();

    await waitFor(() => expect(container.querySelector(".sessions-row")).not.toBeNull());
    await userEvent.click(container.querySelector(".sessions-row") as HTMLElement);
    await screen.findByText(RECOMMENDATION);

    expect(container.querySelector(".prob-value")?.textContent).toBe("unknown");
    expect(container.querySelector(".prob-display")?.textContent).not.toContain("/ 100");
    expect(screen.getByText("Model · unknown")).toBeInTheDocument();
    expect(screen.queryByText(/v5/)).toBeNull();
  });

  // F019: the answer's version is the backend's default, which this page never sent.
  it("shows the model as unknown even though the answer carries the default v5 (F019)", async () => {
    backend();
    renderPage();
    await generate();
    await screen.findByText(RECOMMENDATION);

    expect(screen.getByText("Model · unknown")).toBeInTheDocument();
    expect(screen.queryByText(/v5/)).toBeNull();
  });

  // F021: the stream has no model version, so the streamed result shows none.
  it("shows the model as unknown for a streamed answer, which carries no version (F021)", async () => {
    backend();
    renderPage();
    await streamOnce();
    await screen.findByText("Recommended for funding.");

    expect(screen.getByText("Model · unknown")).toBeInTheDocument();
    expect(screen.queryByText(/v5/)).toBeNull();
  });

  // F020: the top chip is the presentation mode, so it is labelled Mode.
  it("labels the presentation mode 'Mode', not 'Confidence' (F020)", async () => {
    backend();
    const { container } = renderPage();
    await generate();
    await screen.findByText(RECOMMENDATION);

    expect(container.querySelector(".confidence-chip")?.textContent).toBe("Mode · high");
    expect(screen.queryByText(/Confidence/)).toBeNull();
  });

  // F023: 1 - distance is a similarity, so it has two decimals and no percent sign.
  it("shows a retrieval score as a similarity with two decimals and no percent sign (F023)", async () => {
    backend({ copilot: () => json({ ...ANSWER, sources: [source(0.7312)] }) });
    const { container } = renderPage();
    await generate();
    await screen.findByText(RECOMMENDATION);

    const card = container.querySelector(".source-card") as HTMLElement;
    expect(card.querySelector(".score-chip")?.textContent).toBe("similarity 0.73");
    expect(card.textContent).not.toContain("%");
  });

  // F024: a clean screen says "no flags", and the risk is an index with no percent sign.
  it("reports a clean heuristic screen as no flags, with the risk as an index (F024)", async () => {
    backend();
    const { container } = renderPage();
    await generate();
    await screen.findByText(RECOMMENDATION);

    expect(screen.getByText("heuristic screen: no flags")).toBeInTheDocument();
    expect(screen.getByText("risk index 0.00")).toBeInTheDocument();
    expect(container.querySelector(".sentinel-badge")?.textContent).not.toMatch(/PASS|FAIL|WARN/);
    expect(screen.getByText(/not a compliance certification/)).toBeInTheDocument();
  });

  // F024: findings are counted, and nothing is called a PASS or FAIL.
  it("counts the flags the heuristic screen raised, and never says PASS or FAIL (F024)", async () => {
    backend({
      copilot: () => json({
        ...ANSWER,
        compliance: {
          ...CLEAN_SCREEN,
          bias_findings: [
            { pattern: "certainty_claim", match: "guaranteed", note: "Avoid certainty language" },
            { pattern: "outcome_promise", match: "will succeed", note: "Replace deterministic claim" },
          ],
          risk_score: 0.3,
        },
      }),
    });
    const { container } = renderPage();
    await generate();
    await screen.findByText(RECOMMENDATION);

    expect(screen.getByText("heuristic screen: 2 flags")).toBeInTheDocument();
    expect(screen.getByText("risk index 0.30")).toBeInTheDocument();
    expect(container.querySelector(".sentinel-badge")?.textContent).not.toMatch(/PASS|FAIL/);
  });

  // F024: an empty text is not a pass; the screen says it scanned nothing.
  it("says the screen scanned no text rather than passing an empty answer (F024)", async () => {
    backend({ copilot: () => json({ ...ANSWER, compliance: { ...CLEAN_SCREEN, redacted_text: "" } }) });
    renderPage();
    await generate();
    await screen.findByText(RECOMMENDATION);

    expect(screen.getByText("heuristic screen: no text scanned")).toBeInTheDocument();
    expect(screen.queryByText("heuristic screen: no flags")).toBeNull();
  });

  // F078: a successful response without a sessions list is a failed read.
  it("says the chats could not be read when the sessions response has no list (F078)", async () => {
    backend({ sessions: () => json({}) });
    const { container } = renderPage();
    const list = () => container.querySelector(".sessions-list")?.textContent ?? "";

    await waitFor(() => expect(list()).toContain("Could not read sessions"));
    expect(list()).not.toContain("No chats yet");
  });

  // F022: a real zero is 0 in the label, the probability and the similarity.
  it("shows a real zero as 0, not as the 0.50 or 50 fallback (F022)", async () => {
    backend({ copilot: () => json({ ...ANSWER, probability: 0, sources: [source(0)] }) });
    const { container } = renderPage();

    const slider = container.querySelector('input[type="range"]') as HTMLInputElement;
    fireEvent.change(slider, { target: { value: "0" } });
    expect(screen.getByText("Probability (0.00)")).toBeInTheDocument();

    await generate();
    await screen.findByText(RECOMMENDATION);
    expect(container.querySelector(".prob-value")?.textContent).toBe("0");
    expect(container.querySelector(".score-chip")?.textContent).toBe("similarity 0.00");
  });

  // The presets are on the API scale. The explain endpoint rejects the old 0-100
  // values (85, 60, 30) with a 422, so each preset must now get an explanation.
  it.each([
    ["HIGH", 8.5],
    ["MODERATE", 6],
    ["LOW", 3],
  ])("the %s preset sends social impact %s to both endpoints and gets an explanation", async (preset, social) => {
    const stub = backend();
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: preset }));
    await screen.findByText(RECOMMENDATION);

    expect(callsTo(stub, "/predict/explain")[0].body).toMatchObject({ social_impact_score: social });
    const copilot = callsTo(stub, "/copilot/explain")[0].body as { features: { social_impact: number }; shap_values: unknown };
    expect(copilot.features.social_impact).toBe(social);
    expect(copilot.shap_values).toEqual(SHAP_SENT);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("the social impact field takes 0-10 in steps of 0.5, as the API does", () => {
    const { container } = renderPage();
    const input = container.querySelector('input[name="social_impact"]') as HTMLInputElement;
    expect(screen.getByText("Social impact (0–10)")).toBeInTheDocument();
    expect(input).toHaveAttribute("min", "0");
    expect(input).toHaveAttribute("max", "10");
    expect(input).toHaveAttribute("step", "0.5");
  });

  // Two chips both reading "Mode" told the user nothing about which was which.
  it("labels the bottom chip 'Explanation mode', so the top 'Mode' chip is not repeated", async () => {
    backend();
    const { container } = renderPage();
    await generate();
    await screen.findByText(RECOMMENDATION);

    expect(container.querySelector(".confidence-chip")?.textContent).toBe("Mode · high");
    const chips = [...container.querySelectorAll(".mode-chip")].map((c) => c.textContent);
    expect(chips).toEqual(["Explanation mode · smart_template", "Model · unknown"]);
  });

  // Stop must reach the request. It aborts the stream's signal and ends the streaming
  // state, keeping what arrived, marked (stopped), and not reported as a failure.
  it("Stop aborts the stream request, ends streaming, and keeps the partial answer marked (stopped)", async () => {
    // A holder rather than a let: TypeScript narrows a let that only a callback assigns.
    const seen: { signal?: AbortSignal } = {};
    backend({
      stream: (init) => {
        seen.signal = init?.signal ?? undefined;
        return hangingStream(init);
      },
    });
    renderPage();
    await streamOnce();
    await screen.findByText(/Recommended for/);
    expect(seen.signal?.aborted).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Stop" }));

    await waitFor(() => expect(seen.signal?.aborted).toBe(true));
    await waitFor(() => expect(screen.queryByText(/Streaming\.\.\./)).toBeNull());
    expect(screen.getByText("(stopped)")).toBeInTheDocument();
    expect(screen.getByText(/Recommended for/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });
});
