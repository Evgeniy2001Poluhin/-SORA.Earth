import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import WebhooksPage from "./WebhooksPage";
import { renderWithQuery } from "@/test/utils";

const SUBS = [
  { id: 1, url: "https://hook.example.com/a", event_type: "drift", active: true, created_at: "2026-05-01T00:00:00Z" },
];
const DELS = [
  { id: 9, subscription_id: 1, event_type: "drift", status_code: 200, ok: true, error: "", created_at: "2026-05-02T00:00:00Z" },
];

/** Route each webhook endpoint to a caller-supplied handler. */
function stubFetch(
  handler: (url: string, method: string) => { ok: boolean; status: number; body: unknown },
) {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const { ok, status, body } = handler(url, init?.method ?? "GET");
    return {
      ok,
      status,
      json: async () => {
        // A body of null stands for "nothing parseable came back", which is what
        // a 502 from a proxy looks like.
        if (body === null) throw new SyntaxError("Unexpected end of JSON input");
        return body;
      },
    } as Response;
  }) as unknown as typeof fetch;
}

describe("WebhooksPage with TanStack Query", () => {
  beforeEach(() => {
    stubFetch((url) => ({ ok: true, status: 200, body: url.includes("/deliveries") ? DELS : SUBS }));
  });

  it("renders subscriptions and deliveries once the queries settle", async () => {
    renderWithQuery(<WebhooksPage />);

    await waitFor(() => expect(screen.getByText("https://hook.example.com/a")).toBeInTheDocument());
    expect(screen.getByText("Active subscriptions")).toBeInTheDocument();
    expect(screen.getByText("Recent deliveries")).toBeInTheDocument();
  });

  it("shows empty states when both endpoints return nothing", async () => {
    stubFetch(() => ({ ok: true, status: 200, body: [] }));
    renderWithQuery(<WebhooksPage />);

    await waitFor(() => expect(screen.getByText("No subscriptions yet")).toBeInTheDocument());
    expect(screen.getByText("No deliveries yet")).toBeInTheDocument();
  });

  it("keeps the empty state instead of crashing when a request fails", async () => {
    stubFetch(() => {
      throw new Error("network down");
    });
    renderWithQuery(<WebhooksPage />);

    await waitFor(() => expect(screen.getByText("No subscriptions yet")).toBeInTheDocument());
  });

  it("issues one request per query key and does not refetch in a loop", async () => {
    renderWithQuery(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText("https://hook.example.com/a")).toBeInTheDocument());

    const calls = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length;
    await new Promise((r) => setTimeout(r, 150));
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(calls);
    expect(calls).toBe(2);
  });

  // ------------------------------------------------------- failure is not success
  //
  // fetch resolves for 4xx and 5xx, and .json() on a `{"detail": ...}` body
  // succeeds. Before these, that body became the query's data and the POST read
  // `secret` off it -- so every one of these cases looked like it had worked.

  it("says so when the subscriptions request is refused, instead of showing an empty list", async () => {
    stubFetch(() => ({ ok: false, status: 403, body: { detail: "admin token required" } }));
    renderWithQuery(<WebhooksPage />);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("admin token required"));
    // The empty state alone would be indistinguishable from having no webhooks.
    expect(screen.getByText(/Could not load webhooks/)).toBeInTheDocument();
  });

  it("does not crash when a refused request returns an object where a list was expected", async () => {
    // Asserting on the heading alone would not discriminate: it renders before
    // the bad data arrives, so the assertion passes and the crash happens after.
    // React reports an uncaught render error through console.error, so that is
    // what gets watched.
    const errors: string[] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...args) => {
      errors.push(args.map(String).join(" "));
    });
    try {
      stubFetch(() => ({ ok: false, status: 400, body: { detail: "bad request" } }));
      renderWithQuery(<WebhooksPage />);

      await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
      // subs.map on an object threw and took the whole page down with it.
      expect(errors.filter((e) => e.includes("is not a function"))).toEqual([]);
      expect(screen.getByText("No subscriptions yet")).toBeInTheDocument();
    } finally {
      spy.mockRestore();
    }
  });

  it("reports a rejected subscription and keeps the url in the box", async () => {
    stubFetch((url, method) =>
      method === "POST"
        ? { ok: false, status: 422, body: { detail: "url must be https" } }
        : { ok: true, status: 200, body: url.includes("/deliveries") ? DELS : SUBS },
    );
    renderWithQuery(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText("Active subscriptions")).toBeInTheDocument());

    const box = screen.getByPlaceholderText("https://your-endpoint.com/hook");
    await userEvent.type(box, "http://insecure.example.com/hook");
    await userEvent.click(screen.getByRole("button", { name: "Subscribe" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("url must be https"));
    expect(box).toHaveValue("http://insecure.example.com/hook");
    expect(screen.queryByText(/shown once/)).not.toBeInTheDocument();
  });

  it("shows the secret once and clears the box when the subscription is accepted", async () => {
    stubFetch((url, method) =>
      method === "POST"
        ? { ok: true, status: 201, body: { id: 2, secret: "whsec_abc123" } }
        : { ok: true, status: 200, body: url.includes("/deliveries") ? DELS : SUBS },
    );
    renderWithQuery(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText("Active subscriptions")).toBeInTheDocument());

    const box = screen.getByPlaceholderText("https://your-endpoint.com/hook");
    await userEvent.type(box, "https://hook.example.com/b");
    await userEvent.click(screen.getByRole("button", { name: "Subscribe" }));

    await waitFor(() => expect(screen.getByText("whsec_abc123")).toBeInTheDocument());
    expect(box).toHaveValue("");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not present a missing secret as a secret", async () => {
    stubFetch((url, method) =>
      method === "POST"
        ? { ok: true, status: 201, body: { id: 2 } }
        : { ok: true, status: 200, body: url.includes("/deliveries") ? DELS : SUBS },
    );
    renderWithQuery(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText("Active subscriptions")).toBeInTheDocument());

    await userEvent.type(screen.getByPlaceholderText("https://your-endpoint.com/hook"), "https://h.example/c");
    await userEvent.click(screen.getByRole("button", { name: "Subscribe" }));

    await waitFor(() => expect(screen.getByPlaceholderText("https://your-endpoint.com/hook")).toHaveValue(""));
    expect(screen.queryByText(/shown once/)).not.toBeInTheDocument();
  });

  it("reports a refused delete rather than leaving the row unexplained", async () => {
    stubFetch((url, method) =>
      method === "DELETE"
        ? { ok: false, status: 404, body: { detail: "no such subscription" } }
        : { ok: true, status: 200, body: url.includes("/deliveries") ? DELS : SUBS },
    );
    renderWithQuery(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText("https://hook.example.com/a")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "delete" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("no such subscription"));
  });

  it("falls back to the status when a refusal carries no detail", async () => {
    stubFetch((url, method) =>
      method === "DELETE"
        ? { ok: false, status: 502, body: null }
        : { ok: true, status: 200, body: url.includes("/deliveries") ? DELS : SUBS },
    );
    renderWithQuery(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText("https://hook.example.com/a")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "delete" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("502"));
  });

  it("disables Subscribe until a url is entered", async () => {
    renderWithQuery(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText("Active subscriptions")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: "Subscribe" })).toBeDisabled();
  });
});
