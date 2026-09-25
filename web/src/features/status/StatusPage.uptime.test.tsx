/**
 * The public status page shows what stands behind each uptime figure.
 *
 * `uptime_24h` was `ok rows / observed rows`, and the only writer of
 * `health_pings` is `record_health()`, which runs inside the process being
 * measured. An outage therefore leaves **absence**, not `ok=False`, and absence
 * never entered the denominator. Measured 2026-09-25 over a 24-hour window at
 * the scheduler's own 5-minute cadence, six hours of which held no sample at
 * all: the page reported **100.0%**. The control -- 72 samples present and
 * saying not-ok -- reported 75.0, so the reader was fine and the denominator
 * was not.
 *
 * The backend now counts slots one sample interval wide, which makes the figure
 * a lower bound, and returns `coverage_*` beside it. This page has to render
 * that: a lower bound shown alone is still read as a measurement.
 *
 * The footer also claimed "uptime sampled every 5 min" as a literal while the
 * period lives in `app/scheduler.py`. It now comes from the payload.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import StatusPage from "./StatusPage";
import { renderWithQuery } from "@/test/utils";
import { stubJson } from "@/test/http";

const body = (over: Record<string, unknown> = {}) => ({
  overall: "operational",
  sample_interval_minutes: 5,
  components: [{
    component: "database", ok: true,
    uptime_24h: 75, coverage_24h: 75,
    uptime_7d: 99.4, coverage_7d: 100,
    ...over,
  }],
});

async function textOf(payload: unknown): Promise<string> {
  stubJson(payload);
  const { container, findByText } = renderWithQuery(<StatusPage />);
  await findByText("Database");           // the fetch has resolved
  return container.textContent ?? "";
}

describe("the status page's uptime figures", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("states a range when part of the window holds no sample", async () => {
    const text = await textOf(body());

    // 75% of the window observed and up; the missing quarter could have gone
    // either way, so the truth is between 75 and 100 -- not the flat 100 the
    // old computation printed, and not a bare 75 either.
    expect(text).toContain("75\u2013100%");
    expect(text, "a figure standing on three quarters of the window read as a measurement")
      .toContain("(75% observed)");
  });

  it("does not read a barely sampled component as an outage", async () => {
    // `api` has no sampler on a cadence: the scheduler container cannot observe
    // the API and stopped claiming it, so those rows arrive only when somebody
    // opens this page. A bare lower bound would print 0.69% for a healthy API.
    const text = await textOf(body({ uptime_24h: 0.69, coverage_24h: 0.69 }));

    expect(text).toContain("0.69\u2013100%");
    expect(text).toContain("(0.69% observed)");
  });

  it("stays quiet when the whole window was observed", async () => {
    // The control: without it the rule above would also hold on a page that
    // printed the coverage unconditionally, which is noise on a normal day.
    const text = await textOf(body({ uptime_24h: 100, coverage_24h: 100 }));

    expect(text).toContain("100%");
    expect(text).not.toContain("observed)");
    expect(text, "a fully sampled window is one number, not a range")
      .not.toContain("\u2013");
  });

  it("still shows a dash for a component that was never sampled", async () => {
    const text = await textOf(body({ uptime_24h: null, coverage_24h: 0 }));

    expect(text, "never recorded is not the same as down").toContain("—");
    expect(text).not.toContain("(0% observed)");
  });

  it("reads the sample interval from the payload rather than a literal", async () => {
    const text = await textOf({ ...body(), sample_interval_minutes: 15 });

    expect(text).toContain("every 15 min");
    expect(text, "the page carried '5 min' written into it while the period lives in the scheduler")
      .not.toContain("every 5 min");
  });

  it("claims no cadence when the backend states none", async () => {
    const payload = body() as Record<string, unknown>;
    delete payload.sample_interval_minutes;

    const text = await textOf(payload);

    expect(text).toContain("Auto-refreshes every 30s");
    expect(text).not.toContain("sampled every");
  });

  it("says that an unsampled interval counts against the figure", async () => {
    const text = await textOf(body());

    expect(text).toContain("could have gone");
  });
});
