/**
 * CountryRanking against the production path (#218).
 *
 * `CountryRanking.test.tsx` covers the same component in mock mode and is
 * legitimate: the canned payload's shape is a real contract. What it cannot
 * see is the branch that actually runs on the deployed site — which is how
 * production served invented numbers for weeks with a green suite (#216).
 *
 * So this file forces `isMock: false`, stubs the transport, and asserts the
 * real branch: the request that goes out, the payload that comes back
 * unaltered, and — the part that matters most — that a failing API surfaces
 * a failure and never a number.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

// Production mode for this file only. Hoisted above the imports below, which
// read `isMock` through the endpoint module.
vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import CountryRanking from "./CountryRanking";
import { evaluateApi } from "@/api/endpoints/evaluate";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { callsOf, scoreLikeNumbersIn, stubJson, stubStatus, stubTransportError } from "@/test/http";

const REQUEST = {
  project_name: "Wind Park",
  country: "Germany",
  budget_usd: 220000,
  co2_reduction_tons_per_year: 300,
  social_impact_score: 7,
  project_duration_months: 18,
};

/** Deliberately unlike the mock's output, so a test passing on canned data
 *  instead of this payload is visible rather than plausible. */
const SERVER_RANKING = {
  count: 2,
  ranking: [
    { country: "Narnia", region: "EU", total_score: 11.5, success_probability: 12.5 },
    { country: "Atlantis", region: "EU", total_score: 10.25, success_probability: 11 },
  ],
};

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    // Without this the assertions below would be describing the mock again.
    expect(isMock).toBe(false);
  });
});

describe("CountryRanking on the production path", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("asks the ranking endpoint rather than answering from canned data", async () => {
    const fetchStub = stubJson(SERVER_RANKING);

    await evaluateApi.ranking(REQUEST);

    const [call] = callsOf(fetchStub);
    expect(call.url).toBe("/api/v1/evaluate/ranking");
    expect(call.method).toBe("POST");
    expect(call.body).toMatchObject({ country: "Germany", budget_usd: 220000 });
  });

  it("returns what the server sent, unaltered", async () => {
    stubJson(SERVER_RANKING);

    const data = await evaluateApi.ranking(REQUEST);

    expect(data).toEqual(SERVER_RANKING);
    expect(data.ranking.map((r) => r.country)).toEqual(["Narnia", "Atlantis"]);
  });

  it("renders the server's rows, not the mock's countries", async () => {
    stubJson(SERVER_RANKING);
    const data = await evaluateApi.ranking(REQUEST);

    renderWithQuery(
      <CountryRanking data={data} loading={false} currentCountry="Narnia" onPickCountry={vi.fn()} />,
    );

    expect(screen.getByText("Narnia")).toBeInTheDocument();
    expect(screen.queryByText("Germany")).not.toBeInTheDocument();
  });

  it("a 500 rejects instead of producing a ranking", async () => {
    stubStatus(500);
    await expect(evaluateApi.ranking(REQUEST)).rejects.toThrow(/500/);
  });

  it("a dropped connection rejects instead of producing a ranking", async () => {
    stubTransportError();
    await expect(evaluateApi.ranking(REQUEST)).rejects.toThrow();
  });

  it("the rejection carries no number a caller could render as a score", async () => {
    stubStatus(503);

    const error = await evaluateApi.ranking(REQUEST).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(Error);
    expect(scoreLikeNumbersIn({ ...(error as Error) })).toEqual([]);
  });

  it("an empty answer stays empty rather than being filled in", async () => {
    stubJson({});

    const data = await evaluateApi.ranking(REQUEST);

    expect(data).toEqual({});
    expect(scoreLikeNumbersIn(data)).toEqual([]);
  });

  it("renders no score when the request failed and there is no data", () => {
    // The component's own half of the contract: absent data must render as
    // an empty state, never as a number that looks measured.
    renderWithQuery(
      <CountryRanking data={undefined} loading={false} currentCountry="Germany" onPickCountry={vi.fn()} />,
    );

    expect(screen.queryByText("Narnia")).not.toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toMatch(/\d+\.\d/);
  });
});
