/**
 * Export CSV: the whole history the account may read, through the shared
 * transport, and never a download for an account that has gone.
 *
 * app/api/evaluate.py export_csv ignores the page's filters and exports every
 * permitted row; the request carries none, and the button says "all". A CSV
 * that finishes downloading after a sign-out or account switch belongs to the
 * previous account, and must not be handed to the browser as a file.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { act } from "react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import HistoryPage from "./HistoryPage";
import { useAuth } from "@/store/auth";
import { callsOf } from "@/test/http";
import { SPECIALIST_A, deferred, jsonResponse, renderWithQuery, routeFetch, seedSession } from "@/test/utils";

const EMPTY = { items: [], total: 0, limit: 20, offset: 0 };
const EXPORT = "/api/v1/export/csv";

beforeEach(() => {
  vi.restoreAllMocks();
  seedSession(SPECIALIST_A, "tok-a");
  URL.createObjectURL = vi.fn(() => "blob:csv");
  URL.revokeObjectURL = vi.fn();
});

describe("Export CSV", () => {
  it("asks for the whole readable history, whatever the page filters say", async () => {
    const stub = routeFetch([
      ["GET", /^\/api\/v1\/history\?/, () => jsonResponse(EMPTY)],
      ["GET", EXPORT, () => new Response("ID,Name\n1,a\n", { status: 200, headers: { "content-type": "text/csv" } })],
    ]);
    renderWithQuery(<MemoryRouter><HistoryPage /></MemoryRouter>);
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText("Region…"), "Europe");
    await user.click(screen.getByRole("button", { name: "HIGH" }));
    await user.click(screen.getByRole("button", { name: /Export CSV/ }));

    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));
    const call = callsOf(stub).find((c) => c.url.startsWith(EXPORT))!;
    expect(call.url).toBe(EXPORT);
    expect(call.headers["Authorization"]).toBe("Bearer tok-a");
    expect((stub.mock.calls.find(([u]) => String(u) === EXPORT)![1] as RequestInit).cache).toBe("no-store");
  });

  it("does not download A's file when the response is in but its body finishes after A signed out", async () => {
    // fetch resolved inside A's session; only reading the body outlives it --
    // the case an abort cannot stop and a check after fetch does not see.
    const file = deferred<Blob>();
    routeFetch([
      ["GET", /^\/api\/v1\/history\?/, () => jsonResponse(EMPTY)],
      ["GET", EXPORT, () => ({ ok: true, status: 200, blob: () => file.promise }) as unknown as Response],
    ]);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderWithQuery(<MemoryRouter><HistoryPage /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    await new Promise((r) => setTimeout(r, 10));

    await act(async () => { await useAuth.getState().logout(); });
    await act(async () => {
      file.resolve(new Blob(["ID,Name\n1,A's row\n"], { type: "text/csv" }));
      await new Promise((r) => setTimeout(r, 20));
    });

    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(click).not.toHaveBeenCalled();
  });

  it("says a 403 is the role, and downloads nothing", async () => {
    routeFetch([
      ["GET", /^\/api\/v1\/history\?/, () => jsonResponse(EMPTY)],
      ["GET", EXPORT, () => new Response("{}", { status: 403 })],
    ]);
    const { container } = renderWithQuery(<MemoryRouter><HistoryPage /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    await waitFor(() => expect(container.querySelector(".hist-error")?.textContent).toContain("not available for your role"));
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
