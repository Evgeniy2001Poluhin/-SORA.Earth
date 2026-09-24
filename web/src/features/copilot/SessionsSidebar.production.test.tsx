/**
 * The chat list when the request fails.
 *
 * `/copilot/sessions` answers 200 with a `sessions` list; a failed request has
 * no answer. The sidebar read `query.data ?? []` and gated its empty state only
 * on "not loading", so a failure told the user "No chats yet" -- that their
 * history is empty, when it could not be read. Absent and empty are different
 * answers (#236).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";

import { SessionsSidebar } from "./SessionsSidebar";
import { renderWithQuery } from "@/test/utils";
import { stubJson, stubStatus } from "@/test/http";

const renderSidebar = () =>
  renderWithQuery(<SessionsSidebar currentId={null} onSelect={() => {}} onNew={() => {}} />);

const listText = (c: HTMLElement) => c.querySelector(".sessions-list")?.textContent ?? "";

describe("SessionsSidebar", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    try { localStorage.clear(); } catch { /* not fatal */ }
  });

  it("does not say there are no chats when the list could not be read", async () => {
    stubStatus(503);
    const { container } = renderSidebar();
    // "Loading…" is on screen until the request settles; wait for it to go.
    await waitFor(() => expect(listText(container)).not.toContain("Loading"));
    expect(listText(container)).not.toContain("No chats yet");
    expect(listText(container)).toMatch(/could not load/i);
  });

  // Controls: the true empty state, and a real list, are unchanged.
  it("still says there are no chats when the server says so", async () => {
    stubJson({ sessions: [] });
    const { container } = renderSidebar();
    await waitFor(() => expect(listText(container)).toContain("No chats yet"));
  });

  it("lists the chats the server returns", async () => {
    stubJson({ sessions: [{ id: "s-1", title: "Solar farm in Sweden", updated_at: "2026-09-20T10:00:00Z", message_count: 4 }] });
    const { container } = renderSidebar();
    await waitFor(() => expect(listText(container)).toContain("Solar farm in Sweden"));
    expect(listText(container)).not.toContain("No chats yet");
  });
});
