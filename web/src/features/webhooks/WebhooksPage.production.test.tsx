/**
 * The webhooks page while a list is loading, when its request fails, and when
 * the server genuinely has none.
 *
 * Both queries defaulted `data` to `[]`, so the page could not tell "no answer
 * yet" or "the request was refused" from "the server has no subscriptions", and
 * printed "No subscriptions yet" beside the failure it was reporting. The
 * endpoints are app/api/webhooks.py: `GET /api/v1/webhooks` answers a list, and
 * `GET /api/v1/webhooks/deliveries` is admin-only (line 59), so a signed-in
 * viewer who is not an admin gets a refusal for that list and an answer for the
 * other.
 *
 * Measured with transport stubs, not deduced.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import WebhooksPage from "./WebhooksPage";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";

const SUB = {
  id: 1, url: "https://hook.example.com/a", event_type: "drift", active: true,
  created_at: "2026-05-01T00:00:00Z",
};
const DEL = {
  id: 9, subscription_id: 1, event_type: "drift", status_code: 200, ok: true, error: "",
  created_at: "2026-05-02T00:00:00Z",
};

type Answer = { status: number; body: unknown } | "pending";

/** Answer the two webhook endpoints separately, as the server does. */
function serve(subs: Answer, dels: Answer) {
  vi.spyOn(globalThis, "fetch").mockImplementation((async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const answer = url.endsWith("/deliveries") ? dels : subs;
    if (answer === "pending") return new Promise<Response>(() => {});
    const { status, body } = answer;
    return typeof body === "string"
      ? new Response(body, { status })
      : new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }) as typeof fetch);
}

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("WebhooksPage while the answer is still on its way", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("says the lists are loading, not that there are none", async () => {
    serve("pending", "pending");
    renderWithQuery(<WebhooksPage />);

    expect(await screen.findByText("Loading subscriptions…")).toBeInTheDocument();
    expect(screen.getByText("Loading deliveries…")).toBeInTheDocument();
    expect(screen.queryByText("No subscriptions yet")).not.toBeInTheDocument();
    expect(screen.queryByText("No deliveries yet")).not.toBeInTheDocument();
  });
});

describe("WebhooksPage when a request fails", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("says the subscriptions are unavailable, not that there are none", async () => {
    serve({ status: 500, body: "upstream said no" }, { status: 200, body: [] });
    renderWithQuery(<WebhooksPage />);

    expect(await screen.findByText("Subscriptions unavailable: the request failed.")).toBeInTheDocument();
    expect(screen.queryByText("No subscriptions yet")).not.toBeInTheDocument();
    // Control: the deliveries really were empty, so their empty state is true.
    expect(await screen.findByText("No deliveries yet")).toBeInTheDocument();
  });

  it("says the deliveries are unavailable when the admin-only list is refused", async () => {
    serve({ status: 200, body: [SUB] }, { status: 403, body: { detail: "admin token required" } });
    renderWithQuery(<WebhooksPage />);

    expect(await screen.findByText("Deliveries unavailable: the request failed.")).toBeInTheDocument();
    expect(screen.queryByText("No deliveries yet")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("admin token required");
    // Control: the subscriptions the server did send are still listed.
    expect(screen.getByText("https://hook.example.com/a")).toBeInTheDocument();
  });
});

describe("WebhooksPage with real answers", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("says there are none when the server answers an empty list", async () => {
    // The control for every case above: a true empty state must still read as one.
    serve({ status: 200, body: [] }, { status: 200, body: [] });
    renderWithQuery(<WebhooksPage />);

    expect(await screen.findByText("No subscriptions yet")).toBeInTheDocument();
    expect(screen.getByText("No deliveries yet")).toBeInTheDocument();
    expect(screen.queryByText(/unavailable/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
  });

  it("shows the rows the server sent, with no empty state or loading line", async () => {
    serve({ status: 200, body: [SUB] }, { status: 200, body: [DEL] });
    renderWithQuery(<WebhooksPage />);

    expect(await screen.findByText("https://hook.example.com/a")).toBeInTheDocument();
    expect(screen.queryByText("No subscriptions yet")).not.toBeInTheDocument();
    expect(screen.queryByText("No deliveries yet")).not.toBeInTheDocument();
    expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
  });
});
