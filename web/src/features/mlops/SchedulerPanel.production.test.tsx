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

  it("does not say the scheduler is disabled or idle when it does not know", async () => {
    stubStatus(UNREACHABLE);
    renderPanel();

    // Waits for the status query to leave LOADING, and not for UNKNOWN. Waiting
    // for UNKNOWN would make the IDLE check below true by construction. Enabled
    // reads "—" on origin/main too, so only the Status tile separates the versions.
    await waitFor(() => expect(kpi("Status")).not.toBe("LOADING"));
    expect(kpi("Enabled")).toBe("—");
    expect(kpi("Enabled")).not.toBe("NO");
    expect(kpi("Status")).not.toBe("IDLE");
  });

  it("does not report zero runs when the count could not be read", async () => {
    stubStatus(UNREACHABLE);
    renderPanel();

    await waitFor(() => expect(kpi("Status")).toBe("UNKNOWN"));
    expect(kpi("History")).not.toBe("0");
    expect(kpi("History")).toBe("—");
  });

  it("does not report zero jobs when the fallback answered", async () => {
    // The fallback sends `jobs: []` about a container it never reached.
    // That empty list is not a count of the scheduler's jobs.
    stubStatus(UNREACHABLE);
    renderPanel();

    await waitFor(() => expect(kpi("Status")).toBe("UNKNOWN"));
    expect(kpi("Jobs")).toBe("—");
    expect(kpi("Jobs")).not.toBe("0");
  });

  it("does not report idle when the fallback answered running: false", async () => {
    stubStatus(UNREACHABLE);
    renderPanel();

    await waitFor(() => expect(kpi("Status")).toBe("UNKNOWN"));
    expect(kpi("Status")).not.toBe("IDLE");
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

/** What one endpoint answers in a test: a JSON body, an HTTP failure, or
 *  nothing yet. The panel issues two queries, so each gets its own answer. */
type Answer = { body: unknown } | { failWith: number } | "pending";

/** Routes the status and the history queries separately. A later call replaces
 *  the earlier stub, which is how a test makes a refresh fail. */
function route(scheduler: Answer, history: Answer) {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const answer = String(input).includes("/scheduler/status") ? scheduler : history;
    if (answer === "pending") return new Promise<Response>(() => {});
    if ("failWith" in answer) return new Response("upstream said no", { status: answer.failWith });
    return new Response(JSON.stringify(answer.body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

describe("SchedulerPanel while the answer is not in", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAuth.setState({ user: { username: "admin", role: "admin" } });
  });

  it("says loading, not idle, before the first status answer", () => {
    route("pending", "pending");
    renderPanel();

    expect(kpi("Status")).toBe("LOADING");
    expect(kpi("Status")).not.toBe("IDLE");
  });

  it("says unknown, not idle, when the status request fails", async () => {
    route({ failWith: 500 }, { body: [] });
    renderPanel();

    await waitFor(() => expect(kpi("Status")).toBe("UNKNOWN"));
    expect(kpi("Status")).not.toBe("IDLE");
  });

  it("says unknown when the answer carries no running flag", async () => {
    route(
      { body: { jobs: [], retrain_history_count: 3, source: "scheduler_container" } },
      { body: [] },
    );
    renderPanel();

    await waitFor(() => expect(kpi("Status")).toBe("UNKNOWN"));
    expect(kpi("Status")).not.toBe("IDLE");
  });

  it("does not keep showing the last good status once a refresh fails", async () => {
    route({ body: REACHABLE }, { body: [] });
    const { queryClient } = renderPanel();
    await waitFor(() => expect(kpi("Status")).toBe("RUNNING"));

    route({ failWith: 500 }, { body: [] });
    await queryClient.invalidateQueries({ queryKey: ["sched-status"] });

    // TanStack keeps the last successful data through an error. Reading the
    // data without checking the error would keep saying RUNNING here.
    await waitFor(() => expect(kpi("Status")).toBe("UNKNOWN"));
    expect(kpi("Status")).not.toBe("RUNNING");
  });

  it("still reads IDLE when the scheduler says it is not running", async () => {
    // The control for the unknown cases above: a real idle answer stays idle.
    route(
      {
        body: {
          ...REACHABLE,
          running: false,
          jobs: [],
          jobs_count: 0,
          retrain_history_count: 0,
        },
      },
      { body: [] },
    );
    renderPanel();

    await waitFor(() => expect(kpi("Status")).toBe("IDLE"));
    expect(kpi("Jobs")).toBe("0");
    expect(kpi("History")).toBe("0");
  });

  it("still reads zero jobs when the scheduler really has none", async () => {
    route({ body: { ...REACHABLE, jobs: [], jobs_count: 0 } }, { body: [] });
    renderPanel();

    await waitFor(() => expect(kpi("Status")).toBe("RUNNING"));
    expect(kpi("Jobs")).toBe("0");
  });
});

/** One finished retrain, as the history endpoint sends it. */
const HISTORY_ROW = {
  id: 1,
  started_at: "2026-10-01T03:00:00Z",
  finished_at: "2026-10-01T03:02:00Z",
  duration_sec: 120,
  status: "success",
  trigger_source: "scheduled",
  job_name: "closed_loop_daily",
  model_version: "v9",
  data_version: null,
  message: null,
  error_message: null,
  metrics: null,
};

describe("SchedulerPanel run history", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAuth.setState({ user: { username: "admin", role: "admin" } });
  });

  it("does not keep showing the last good rows once a refresh fails", async () => {
    route({ body: REACHABLE }, { body: [HISTORY_ROW] });
    const { queryClient } = renderPanel();
    await waitFor(() => expect(screen.getByText("closed_loop_daily")).toBeInTheDocument());

    route({ body: REACHABLE }, { failWith: 500 });
    await queryClient.invalidateQueries({ queryKey: ["sched-hist"] });

    // The rows are the last successful answer, not the current one.
    await waitFor(() =>
      expect(screen.getByText("Run history unavailable")).toBeInTheDocument(),
    );
    expect(screen.queryByText("closed_loop_daily")).toBeNull();
  });

  it("still lists the rows when the history answers", async () => {
    route({ body: REACHABLE }, { body: [HISTORY_ROW] });
    renderPanel();

    await waitFor(() => expect(screen.getByText("closed_loop_daily")).toBeInTheDocument());
    expect(screen.queryByText("No history yet")).toBeNull();
  });

  it("says the history could not be loaded, not that there is none", async () => {
    route({ body: REACHABLE }, { failWith: 500 });
    renderPanel();

    await waitFor(() =>
      expect(screen.getByText("Run history unavailable")).toBeInTheDocument(),
    );
    expect(screen.queryByText("No history yet")).toBeNull();
  });

  it("says loading, not empty, while the history is pending", async () => {
    route({ body: REACHABLE }, "pending");
    renderPanel();

    await waitFor(() => expect(kpi("Status")).toBe("RUNNING"));
    expect(screen.getByText("Loading run history...")).toBeInTheDocument();
    expect(screen.queryByText("No history yet")).toBeNull();
  });

  it("still says there is no history when the answer is an empty list", async () => {
    route({ body: REACHABLE }, { body: [] });
    renderPanel();

    await waitFor(() => expect(screen.getByText("No history yet")).toBeInTheDocument());
  });
});
