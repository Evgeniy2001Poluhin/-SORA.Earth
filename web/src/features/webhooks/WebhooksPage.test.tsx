import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";

import WebhooksPage from "./WebhooksPage";
import { renderWithQuery } from "@/test/utils";

const SUBS = [
  { id: 1, url: "https://hook.example.com/a", event_type: "drift", active: true, created_at: "2026-05-01T00:00:00Z" },
];
const DELS = [
  { id: 9, subscription_id: 1, event_type: "drift", status_code: 200, ok: true, error: "", created_at: "2026-05-02T00:00:00Z" },
];

/** Route each webhook endpoint to a caller-supplied handler. */
function stubFetch(handler: (url: string) => { ok: boolean; status: number; body: unknown }) {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const { ok, status, body } = handler(url);
    return { ok, status, json: async () => body } as Response;
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

  it("disables Subscribe until a url is entered", async () => {
    renderWithQuery(<WebhooksPage />);
    await waitFor(() => expect(screen.getByText("Active subscriptions")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: "Subscribe" })).toBeDisabled();
  });
});
