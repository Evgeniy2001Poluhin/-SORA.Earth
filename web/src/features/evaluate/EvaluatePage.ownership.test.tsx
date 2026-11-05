/**
 * Calculate, and Calculate and save, against the ownership contract:
 *
 *   POST /evaluate             200, saved=false -- writes nothing
 *   POST /evaluate?save=true   201, saved=true, evaluation_id -- specialist and admin
 *
 * A save is shown as saved only on that whole answer. Everything else -- a
 * wrong status, a body that does not say saved, no id, a dropped connection --
 * is "not confirmed", with no id on screen and nothing sent again.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { EvaluatePage } from "./EvaluatePage";
import { isMock } from "@/api/mock";
import { callsOf } from "@/test/http";
import { ADMIN, ANALYST, SPECIALIST_A, deferred, jsonResponse, renderWithQuery, routeFetch, seedSession } from "@/test/utils";
import type { UserInfo } from "@/api/types";

vi.setConfig({ testTimeout: 15_000 });

const SCORED = { total_score: 61.5, environment_score: 55.5, social_score: 70.5, economic_score: 58.5,
  risk_level: "Medium", success_probability: 66.5, recommendations: [], region: "Europe" };
const SAVED = (id: number) => ({ ...SCORED, saved: true, evaluation_id: id });

const SAVE_URL = "/api/v1/evaluate?save=true";
const CALC_URL = "/api/v1/evaluate";

function serve(save: () => Response | Promise<Response>, extra: Parameters<typeof routeFetch>[0] = []) {
  return routeFetch(
    [
      ["POST", SAVE_URL, save],
      ["POST", CALC_URL, () => jsonResponse({ ...SCORED, saved: false, evaluation_id: null })],
      ...extra,
    ],
    () => jsonResponse({}),
  );
}

const renderAs = (user: UserInfo | null) => {
  if (user) seedSession(user, "tok-" + user.username);
  return renderWithQuery(<MemoryRouter><EvaluatePage /></MemoryRouter>);
};
const saveButton = () => screen.getByRole("button", { name: "Calculate and save" });
const saves = (stub: ReturnType<typeof routeFetch>) => callsOf(stub).filter((c) => c.url === SAVE_URL);

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("Calculate computes and saves nothing", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("sends POST /evaluate without save=true, even for a role that can save", async () => {
    const stub = serve(() => jsonResponse(SAVED(1), 201));
    renderAs(SPECIALIST_A);
    await userEvent.click(screen.getByRole("button", { name: "Calculate" }));
    expect(await screen.findByText("Medium risk")).toBeInTheDocument();

    const posts = callsOf(stub).filter((c) => c.method === "POST" && c.url.startsWith(CALC_URL));
    expect(posts.map((c) => c.url)).toContain(CALC_URL);
    expect(saves(stub)).toHaveLength(0);
    expect(screen.queryByText(/Saved as evaluation/)).not.toBeInTheDocument();
  });
});

describe("who is offered a save", () => {
  beforeEach(() => vi.restoreAllMocks());

  it.each([["specialist", SPECIALIST_A], ["admin", ADMIN]])("%s gets the save button", (_r, user) => {
    serve(() => jsonResponse(SAVED(1), 201));
    renderAs(user);
    expect(saveButton()).toBeEnabled();
  });

  it("an analyst gets no save button, and is told why", () => {
    serve(() => jsonResponse(SAVED(1), 201));
    renderAs(ANALYST);
    expect(screen.queryByRole("button", { name: "Calculate and save" })).not.toBeInTheDocument();
    expect(screen.getByText(/Saving is not available for the analyst role/)).toBeInTheDocument();
  });

  it("anonymous gets a sign-in link instead", () => {
    serve(() => jsonResponse(SAVED(1), 201));
    renderAs(null);
    expect(screen.queryByRole("button", { name: "Calculate and save" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login?next=%2Fevaluate");
  });
});

describe("a confirmed save", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("sends ?save=true with the Bearer, no owner fields, and shows the stored answer and its id", async () => {
    const stub = serve(() => jsonResponse(SAVED(42), 201));
    renderAs(SPECIALIST_A);
    await userEvent.click(saveButton());

    expect(await screen.findByText(/Saved as evaluation #42/)).toBeInTheDocument();
    expect(screen.getByText("Medium risk")).toBeInTheDocument();
    const [call] = saves(stub);
    expect(call.headers["Authorization"]).toBe("Bearer tok-spec-a");
    const body = call.body as Record<string, unknown>;
    expect(body).not.toHaveProperty("owner_user_id");
    expect(body).not.toHaveProperty("user_id");
    expect(body).not.toHaveProperty("data_scope");
  });

  it("sends one request for a double click", async () => {
    const answer = deferred<Response>();
    const stub = serve(() => answer.promise);
    renderAs(SPECIALIST_A);
    const user = userEvent.setup();
    await user.dblClick(saveButton());
    await new Promise((r) => setTimeout(r, 20));
    expect(saves(stub)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Calculate" })).toBeDisabled();

    answer.resolve(jsonResponse(SAVED(5), 201));
    expect(await screen.findByText(/Saved as evaluation #5/)).toBeInTheDocument();
    expect(saves(stub)).toHaveLength(1);
  });

  it("sends one request for two clicks before React re-renders the disabled button", async () => {
    // Both clicks land in one batch, so the button is still enabled for the
    // second: only the in-flight guard stands between them and two records.
    const answer = deferred<Response>();
    const stub = serve(() => answer.promise);
    renderAs(SPECIALIST_A);
    const button = saveButton();
    act(() => {
      button.click();
      button.click();
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(saves(stub)).toHaveLength(1);

    answer.resolve(jsonResponse(SAVED(6), 201));
    expect(await screen.findByText(/Saved as evaluation #6/)).toBeInTheDocument();
    expect(saves(stub)).toHaveLength(1);
  });

  it("stays saved when the SHAP explanation fails afterwards", async () => {
    serve(() => jsonResponse(SAVED(42), 201), [
      ["POST", "/api/v1/predict/explain", () => new Response("shap down", { status: 503 })],
      ["POST", "/api/v1/evaluate/ranking", () => new Response("ranking down", { status: 503 })],
    ]);
    renderAs(SPECIALIST_A);
    await userEvent.click(saveButton());

    expect(await screen.findByText(/Saved as evaluation #42/)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText(/Saved as evaluation #42/)).toBeInTheDocument();
    expect(screen.getByText("Medium risk")).toBeInTheDocument();
    expect(screen.queryByText(/Save not confirmed/)).not.toBeInTheDocument();
  });
});

describe("a save that is not confirmed is not shown as one", () => {
  beforeEach(() => vi.restoreAllMocks());

  it.each([
    ["a 200, even saying saved", () => jsonResponse(SAVED(42), 200)],
    ["a 201 that does not say saved", () => jsonResponse({ ...SCORED, saved: false, evaluation_id: 42 }, 201)],
    ["a 201 without an id", () => jsonResponse({ ...SCORED, saved: true, evaluation_id: null }, 201)],
    ["a 201 with a non-integer id", () => jsonResponse({ ...SCORED, saved: true, evaluation_id: "42" }, 201)],
    ["a 503", () => new Response("could not be saved", { status: 503 })],
  ])("%s", async (_label, answer) => {
    const stub = serve(answer);
    renderAs(SPECIALIST_A);
    await userEvent.click(saveButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("Save not confirmed. Check your history");
    expect(screen.queryByText(/#42/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Saved as evaluation/)).not.toBeInTheDocument();
    expect(screen.getByText("No evaluation yet")).toBeInTheDocument();
    expect(saves(stub)).toHaveLength(1);
  });

  it("a dropped connection: not confirmed, no id, nothing retried", async () => {
    const stub = serve(() => { throw new TypeError("Failed to fetch"); });
    renderAs(SPECIALIST_A);
    await userEvent.click(saveButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("Save not confirmed. Check your history");
    await new Promise((r) => setTimeout(r, 50));
    expect(saves(stub)).toHaveLength(1);
    expect(screen.queryByText(/Saved as evaluation/)).not.toBeInTheDocument();
  });

  it("a 403 says the role, not a failure to check", async () => {
    serve(() => new Response(JSON.stringify({ detail: "This role cannot save evaluations" }), { status: 403 }));
    renderAs(SPECIALIST_A);
    await userEvent.click(saveButton());
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Saving is not available for your role"));
  });
});


describe("saved PDF download", () => {
  it("downloads the saved ID without calculating again", async () => {
    const item = {id:42,name:"Persisted Solar",budget:250000,co2_reduction:120,
      social_impact:7,duration_months:24,total_score:61.5,environment_score:55.5,
      social_score:70.5,economic_score:58.5,success_probability:66.5,
      risk_level:"Medium",region:"Europe",created_at:"2026-10-29T10:00:00",
      recommendation:"Stored recommendation",lat:50,lon:10};
    const stub=routeFetch([
      ["GET","/api/v1/history/42",()=>jsonResponse(item)],
      ["GET","/api/v1/history/42/report.pdf",()=>new Response("%PDF-1.4 persisted",{headers:{"content-type":"application/pdf"}})],
    ],()=>jsonResponse({}));
    const create=vi.fn(()=>"blob:saved-pdf"); const revoke=vi.fn();
    Object.defineProperty(URL,"createObjectURL",{configurable:true,value:create});
    Object.defineProperty(URL,"revokeObjectURL",{configurable:true,value:revoke});
    const click=vi.spyOn(HTMLAnchorElement.prototype,"click").mockImplementation(()=>{});
    seedSession(SPECIALIST_A,"tok-spec-a");
    renderWithQuery(<MemoryRouter initialEntries={["/evaluate?snapshot=42"]}><EvaluatePage /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("button",{name:"Download saved PDF"}));
    await waitFor(()=>expect(click).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledTimes(1); expect(revoke).toHaveBeenCalledWith("blob:saved-pdf");
    expect(callsOf(stub).filter(c=>c.method==="POST"&&c.url.includes("/evaluate"))).toHaveLength(0);
    expect(callsOf(stub).filter(c=>c.url.endsWith("/history/42/report.pdf"))).toHaveLength(1);
    click.mockRestore();
  });
});
