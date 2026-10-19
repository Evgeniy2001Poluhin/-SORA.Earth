/**
 * The open map page as an expiry passes (C8 part 4).
 *
 * A score is admissible while the UTC date is before its earliest expiry. A tab opened before that
 * date used to keep showing the score, and its countdown, indefinitely: nothing refetched it, because
 * staleTime is not a refresh timer. These tests run the page with the PRODUCTION QueryClient, under a
 * fake clock, and count only the requests to /map/countries. RussiaMapModal makes its own request
 * while closed, and it is answered here but not counted.
 *
 * Each test moves the clock with `advance` (timers run) or `vi.setSystemTime` (the clock moves and no
 * timer runs, which is what a sleeping machine does), and asserts the text the reader would see.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { createAppQueryClient } from "@/app/queryClient";
import MapPage from "./MapPage";
import { BAND_COLOR, NEUTRAL_COLOR } from "./countryData";

const SECOND = 1_000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;

// --- Fetch: /map/countries is answered by `respond`; anything else is a 404 and is not counted. ---

type Answer = Response | Promise<Response>;
let respond: (call: number) => Answer = () => json({});
let mapCalls = 0;

const json = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
const down = () => new Response("upstream down", { status: 503 });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

// --- Countries, as /map/countries sends them. ---

const SWEDEN_EXPIRES = {
  name: "Sweden", esg: 81.5, band: "leader", code: "SE", lat: 60, lon: 15,
  esg_status: "ok", esg_source: "pinned_table", missing_inputs: [], earliest_expiry: "2027-01-01",
};
const SWEDEN_INCOMPLETE = {
  name: "Sweden", esg: null, band: null, code: "SE", lat: 60, lon: 15,
  esg_status: "incomplete", esg_source: null, missing_inputs: ["gini_index"], earliest_expiry: "2027-01-01",
};
const CHILE_LATER = {
  name: "Chile", esg: 62.5, band: "developing", code: "CL", lat: -33, lon: -70,
  esg_status: "ok", esg_source: "pinned_table", missing_inputs: [], earliest_expiry: "2027-06-01",
};

/** A payload as the backend sends it: the table's earliest expiry is 2027-01-01. */
const payload = (countries: unknown[]) => ({
  total_countries: countries.length,
  bands: { leader: "ESG >= 80", developing: "60-69" },
  table_available: true,
  snapshot_date: "2026-10-18",
  earliest_expiry: "2027-01-01",
  expiry_notice_days: 60,
  countries,
});

// --- Clock and timers. Only the timers and Date are faked: setImmediate stays real, so React and
// the response bodies keep working between steps. ---

function startClock(iso: string) {
  vi.useFakeTimers({
    toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"],
  });
  vi.setSystemTime(new Date(iso));
}

/** Runs timers for `ms` of fake time, as a page left open does. */
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/** Lets resolved responses and query notifications run, without moving the clock. */
async function settle() {
  await act(async () => {
    for (let i = 0; i < 4; i += 1) {
      await new Promise<void>((resolve) => setImmediate(resolve));
      await vi.advanceTimersByTimeAsync(0);
    }
  });
}

/** Runs fake time forward in one-second steps, letting each response be read between steps. A failed
 *  response is read in real time, so its retry timer is set at the fake time when it is read: one
 *  long advance would move the clock past that point before the retry is scheduled. */
async function run(ms: number) {
  for (let elapsed = 0; elapsed < ms; elapsed += SECOND) {
    await advance(Math.min(SECOND, ms - elapsed));
    await settle();
  }
}

/** The tab coming back: visible, then a visibilitychange. */
async function showTab() {
  await act(async () => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

// --- What the reader sees. ---

const text = (container: HTMLElement) => container.textContent ?? "";
const topNames = (container: HTMLElement) =>
  Array.from(container.querySelectorAll(".top-list li b")).map((b) => b.textContent);
/** The fill of each country marker on the map: a band colour for a score, neutral for none. */
const markerFills = (container: HTMLElement) =>
  Array.from(container.querySelectorAll("path.leaflet-interactive")).map((p) => p.getAttribute("fill"));
function statValue(container: HTMLElement, label: string): string | null {
  const em = Array.from(container.querySelectorAll(".map-stats .stat em")).find((e) => e.textContent === label);
  return em?.parentElement?.querySelector("b")?.textContent ?? null;
}

function renderMap() {
  const queryClient = createAppQueryClient();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <MapPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, queryClient };
}

beforeEach(() => {
  mapCalls = 0;
  respond = () => json({});
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (!url.includes("/map/countries")) return json({ detail: "not stubbed here" }, 404);
    mapCalls += 1;
    return respond(mapCalls);
  }) as unknown as typeof fetch;
});

afterEach(() => {
  vi.useRealTimers();
  delete (document as { visibilityState?: unknown }).visibilityState;
});

describe("MapPage across an expiry (C8 part 4)", () => {
  it("(a) withholds a score at the expiry without a remount, refetches, and shows the server's answer", async () => {
    startClock("2026-12-31T23:59:50Z");
    const refresh = deferred<Response>();
    respond = (n) => (n === 1 ? json(payload([SWEDEN_EXPIRES])) : refresh.promise);
    const { container } = renderMap();
    await settle();

    expect(mapCalls).toBe(1);
    expect(text(container)).toContain("top: 81.5");
    expect(markerFills(container)).toEqual([BAND_COLOR.leader]);
    expect(text(container)).toContain("The pinned data expires on 2027-01-01, in 1 day.");

    await advance(15 * SECOND); // 2027-01-01T00:00:05Z
    await settle();

    expect(mapCalls, "one refetch, once the expiry is reached").toBe(2);
    expect(text(container), "withheld before the refetch answers").not.toContain("81.5");
    expect(markerFills(container), "the withheld marker is drawn neutral, not in its band colour")
      .toEqual([NEUTRAL_COLOR]);
    expect(text(container)).toContain("no complete score: N = 0 of 1 countries");
    expect(text(container)).toContain("The pinned data expired on 2027-01-01 (UTC) and is being refreshed.");

    refresh.resolve(json(payload([SWEDEN_INCOMPLETE])));
    await settle();

    expect(text(container), "the server's incomplete answer is shown").toContain("no complete score: N = 0 of 1 countries");
    expect(text(container)).toContain("no score: 1");
    expect(text(container)).not.toContain("81.5");
    expect(mapCalls, "no further refetch after a fresh answer").toBe(2);
  });

  it("(b) says the scores expired when the refresh fails, and shows no expired score", async () => {
    startClock("2026-12-31T23:59:50Z");
    respond = (n) => (n === 1 ? json(payload([SWEDEN_EXPIRES])) : down());
    const { container } = renderMap();
    await settle();

    await run(30 * SECOND); // the expiry, then the refresh, which is refused on every attempt

    expect(mapCalls, "the refresh was tried, and retried").toBeGreaterThan(1);
    expect(text(container)).toContain(
      "ESG map unavailable: the scores expired on 2027-01-01 (UTC) and fresh data could not be loaded.",
    );
    expect(text(container), "the expired score appears nowhere").not.toContain("81.5");
    expect(text(container)).not.toContain("Sweden");
  });

  it("(c) brings the map back with the server's answer once it recovers, on the next wake-up", async () => {
    startClock("2026-12-31T23:59:50Z");
    let serverUp = false;
    respond = (n) => {
      if (n === 1) return json(payload([SWEDEN_EXPIRES]));
      return serverUp ? json(payload([SWEDEN_INCOMPLETE])) : down();
    };
    const { container } = renderMap();
    await settle();
    await run(30 * SECOND);
    expect(text(container)).toContain("unavailable");

    serverUp = true;
    await advance(SECOND); // the clock moves, so the wake-up is a change
    await showTab();
    await settle();

    expect(text(container), "the unavailable state is gone").not.toContain("unavailable");
    expect(text(container)).toContain("no complete score: N = 0 of 1 countries");
    expect(text(container)).not.toContain("81.5");
  });

  it("(d) after the machine sleeps past the expiry, withholds on wake-up, before any timer has run", async () => {
    startClock("2026-12-31T23:59:50Z");
    const refresh = deferred<Response>();
    respond = (n) => (n === 1 ? json(payload([SWEDEN_EXPIRES])) : refresh.promise);
    const { container } = renderMap();
    await settle();
    expect(text(container)).toContain("top: 81.5");

    // The clock moves on, and no timer runs: the expiry timer is still waiting.
    vi.setSystemTime(new Date("2027-01-01T00:05:00Z"));
    expect(mapCalls).toBe(1);

    await showTab();
    await settle();

    expect(text(container), "withheld as soon as the tab is back").not.toContain("81.5");
    expect(text(container)).toContain("being refreshed");
    expect(mapCalls, "a refetch on wake-up").toBe(2);
  });

  it.each(["focus", "pageshow", "online"])(
    "(d) withholds on a window %s after the clock has moved past the expiry, as on a visibility change",
    async (type) => {
      startClock("2026-12-31T23:59:50Z");
      const refresh = deferred<Response>();
      respond = (n) => (n === 1 ? json(payload([SWEDEN_EXPIRES])) : refresh.promise);
      const { container } = renderMap();
      await settle();

      vi.setSystemTime(new Date("2027-01-01T00:05:00Z"));
      await act(async () => {
        window.dispatchEvent(new Event(type));
      });
      await settle();

      expect(text(container)).not.toContain("81.5");
      expect(mapCalls).toBe(2);
    },
  );

  it("(e) counts the countdown down in UTC days with no refetch, and changes at UTC midnight", async () => {
    startClock("2026-12-30T12:00:00Z");
    respond = () => json(payload([SWEDEN_EXPIRES]));
    const { container } = renderMap();
    await settle();
    expect(text(container)).toContain("The pinned data expires on 2027-01-01, in 2 days.");

    await advance(12 * HOUR); // 2026-12-31T00:00:00Z
    await settle();

    expect(text(container)).toContain("The pinned data expires on 2027-01-01, in 1 day.");
    expect(text(container)).not.toContain("in 2 days");
    expect(mapCalls, "the countdown needed no request").toBe(1);
  });

  it("(f) keeps a score whose expiry is later scored and ranked, while withholding the expired one", async () => {
    startClock("2026-12-31T23:59:50Z");
    const refresh = deferred<Response>();
    respond = (n) => (n === 1 ? json(payload([SWEDEN_EXPIRES, CHILE_LATER])) : refresh.promise);
    const { container } = renderMap();
    await settle();

    await advance(15 * SECOND);
    await settle();

    expect(topNames(container), "Chile stays ranked, Sweden does not").toEqual(["Chile"]);
    expect(markerFills(container).sort(), "Chile keeps its band colour, Sweden is neutral")
      .toEqual([BAND_COLOR.developing, NEUTRAL_COLOR].sort());
    expect(statValue(container, "avg ESG"), "Chile alone: 62.5 rounds to 63").toBe("63");
    expect(text(container)).toContain("N = 1 of 2 countries");
    expect(text(container)).not.toContain("81.5");
  });

  it("(g) withholds a response that lands after its expiry, with no timer having run since the page opened", async () => {
    // No data yet, so no expiry timer exists. The clock is past the expiry when the response arrives.
    startClock("2026-12-31T23:59:59Z");
    const first = deferred<Response>();
    respond = (n) => (n === 1 ? first.promise : new Promise<Response>(() => {}));
    const { container } = renderMap();
    await settle();
    expect(mapCalls).toBe(1);

    vi.setSystemTime(new Date("2027-01-01T00:00:02Z")); // no timer runs: the clock moves only
    first.resolve(json(payload([SWEDEN_EXPIRES])));
    await settle();

    expect(text(container), "the expired score is not shown").not.toContain("81.5");
    expect(markerFills(container), "the expired marker is neutral, not in its band colour")
      .toEqual([NEUTRAL_COLOR]);
    expect(topNames(container)).toEqual([]);
    expect(text(container)).toContain("no complete score: N = 0 of 1 countries");
  });

  it("wakes at least once an hour, so no timer waits longer than the browser can count", async () => {
    startClock("2026-12-02T12:00:00Z"); // the expiry is 30 days away
    respond = () => json(payload([SWEDEN_EXPIRES]));
    const timers = vi.spyOn(globalThis, "setTimeout");
    renderMap();
    await settle();

    const waits = timers.mock.calls.map((call) => Number(call[1] ?? 0));
    expect(Math.max(...waits), "no wait exceeds an hour").toBeLessThanOrEqual(HOUR);
    expect(waits, "the hourly wake is armed").toContain(HOUR);
  });

  it("does not start a second refresh while one is in flight", async () => {
    startClock("2026-12-31T23:59:50Z");
    const refresh = deferred<Response>();
    respond = (n) => (n === 1 ? json(payload([SWEDEN_EXPIRES])) : refresh.promise);
    renderMap();
    await settle();
    await advance(15 * SECOND);
    await settle();
    expect(mapCalls).toBe(2);

    await advance(3 * MINUTE); // the minute tick fires three times while the refresh is pending
    await settle();

    expect(mapCalls).toBe(2);
  });

  it("retries once a minute while the refresh keeps failing, one refresh each time", async () => {
    startClock("2026-12-31T23:59:50Z");
    respond = (n) => (n === 1 ? json(payload([SWEDEN_EXPIRES])) : down());
    renderMap();
    await settle();
    await run(30 * SECOND);
    const before = mapCalls;

    await run(MINUTE);

    // One refresh is four attempts under the production retry policy: the first, and three retries.
    expect(mapCalls - before).toBe(4);
  });
});
