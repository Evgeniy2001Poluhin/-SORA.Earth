/**
 * The scheduler panel must not render an unknown as a fact.
 *
 * `get_scheduler_status()` reads the scheduler container's status out of Redis.
 * When Redis is unavailable it falls back to the local scheduler — empty by
 * design in the `app` container, where `RUN_SCHEDULER=false`. That branch used
 * to answer
 *
 *     { running: false, enabled: true, retrain_history_count: 0,
 *       error: "Scheduler container unreachable or not running" }
 *
 * with both middle values asserted rather than read. `enabled` came from
 * `SORA_SCHEDULER`, which nothing in the repository sets, defaulting to "1" —
 * so this panel's KPI read **YES** on every deployment and could not read NO.
 * The count was a literal `0` while the database was perfectly reachable.
 *
 * The backend now omits `enabled` and counts the runs it can count
 * (`tests/test_the_fallback_status_reports_what_it_knows.py`). That moves the
 * defect here: `{s?.enabled ? "YES" : "NO"}` renders an absent value as **NO**,
 * and `{s?.retrain_history_count ?? 0}` renders an unreadable count as **0**.
 *
 * Absent and false are different answers, and so are absent and zero. It is the
 * same distinction `EvaluatePage.production.test.tsx` was written for (#236),
 * one screen over.
 *
 * This panel had no test of any kind before this file: nothing in `src` set an
 * auth user, and it renders "Sign in required" without one.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

import { SchedulerPanel } from "./SchedulerPanel";
import { isMock } from "@/api/mock";
import { useAuth } from "@/store/auth";
import { renderWithQuery } from "@/test/utils";

/** The payload the fallback branch now sends: no `enabled`, count unreadable. */
const UNREACHABLE = {
  running: false,
  jobs: [],
  jobs_count: 0,
  error: "Scheduler container unreachable or not running",
  source: "local_fallback",
  retrain_history_count: null,
};

/** A reachable scheduler, for the control below. */
const REACHABLE = {
  running: true,
  enabled: true,
  jobs: [{ id: "auto_openmeteo_ingestion" }],
  jobs_count: 1,
  source: "scheduler_container",
  retrain_history_count: 7,
};

/** Assigns `globalThis.fetch` directly, which is what `src/test/http.ts` does.
 *  `vi.stubGlobal` did not reach `src/api/client.ts`, and the control test is
 *  what showed it: every value read its fallback because the query never
 *  resolved. Routed by path because the panel issues two queries. */
function stubStatus(payload: unknown) {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const body = String(input).includes("/scheduler/status") ? payload : [];
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

const renderPanel = () => renderWithQuery(<SchedulerPanel />);

/** The KPI value beside a label, once the query has actually resolved.
 *
 * Reading it straight after `waitFor(getByText(label))` reads it too early:
 * the label is rendered unconditionally, so the wait returns while `s` is
 * still undefined and every value shows its fallback. The control test caught
 * exactly that — it expected YES for `enabled: true` and read NO. */
function kpi(label: string): string {
  // Scoped to `.kpi-lbl`: the retrain history table below the grid has a
  // "Status" column header, so a plain text query matches two elements and
  // throws. Found by running it.
  const labelNode = screen
    .getAllByText(label)
    .find((el) => el.className.includes("kpi-lbl"));
  const node = labelNode?.parentElement?.querySelector(".kpi-val");
  return (node?.textContent ?? "").trim();
}

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("SchedulerPanel when the scheduler container cannot be reached", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAuth.setState({ user: { username: "admin", role: "admin" } });
  });

  it("does not say the scheduler is disabled when it does not know", async () => {
    stubStatus(UNREACHABLE);
    renderPanel();

    await waitFor(() => expect(kpi("Jobs")).toBe("0"));
    expect(kpi("Enabled")).not.toBe("NO");
    expect(kpi("Enabled")).toBe("—");
  });

  it("does not report zero runs when the count could not be read", async () => {
    stubStatus(UNREACHABLE);
    renderPanel();

    await waitFor(() => expect(kpi("Status")).toBe("IDLE"));
    expect(kpi("History")).not.toBe("0");
    expect(kpi("History")).toBe("—");
  });

  it("still shows a real answer when there is one", async () => {
    // The control. Without it, rendering "—" unconditionally would pass both
    // checks above while telling an operator nothing at all.
    stubStatus(REACHABLE);
    renderPanel();

    await waitFor(() => expect(kpi("Status")).toBe("RUNNING"));
    expect(kpi("Enabled")).toBe("YES");
    expect(kpi("History")).toBe("7");
  });
});
