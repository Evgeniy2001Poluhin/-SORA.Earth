import { useEffect, useState } from "react";
import { expiredSinceFetch, nextUtcMidnightAfter, utcDayStartMs } from "./countryData";

/** The longest one timer may wait. `setTimeout` overflows above 2^31-1 ms, so the wait is capped. */
const MAX_WAIT_MS = 3_600_000;

/** How often a page holding expired data it has not replaced checks again. */
const RETRY_MS = 60_000;

/**
 * The page clock: `Date.now()`, kept current at four kinds of moment.
 *
 * 1. The next expiry the held data carries, the table's or a scored country's, and the next
 *    UTC midnight, which moves the countdown. Timers are re-armed after each one.
 * 2. Every minute, while the held data predates an expiry that has passed. A failed refresh is
 *    then retried, and a successful one stops the ticks.
 * 3. On a wake-up: the tab becoming visible, window focus, pageshow, online. Timers are delayed
 *    while a tab is in the background or the machine sleeps, so these are what reach the page
 *    first when it comes back.
 *
 * The hook fetches nothing. A clock change is what the caller acts on.
 *
 * @param expiryDates ISO dates the held data stops being admissible on (`expiryDatesOf`).
 * @param fetchedAtMs when the held data was fetched (the query's `dataUpdatedAt`, 0 for none).
 */
export function useUtcNow(expiryDates: readonly string[], fetchedAtMs: number): number {
  const [nowMs, setNowMs] = useState(() => Date.now());
  // A string key, so the timers are re-armed when the dates change and not on every render.
  const dateKey = expiryDates.join(",");

  useEffect(() => {
    const wake = () => setNowMs(Date.now());
    const onVisibility = () => {
      if (document.visibilityState === "visible") wake();
    };
    window.addEventListener("focus", wake);
    window.addEventListener("pageshow", wake);
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", wake);
      window.removeEventListener("pageshow", wake);
      window.removeEventListener("online", wake);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  useEffect(() => {
    const dates = dateKey === "" ? [] : dateKey.split(",");
    let timer: ReturnType<typeof setTimeout> | undefined;
    // New data may have arrived after its expiry passed, with no timer to say so: catch the clock up.
    // One extra render per change of data, not a loop: the clock is external, read once here.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNowMs(Date.now());
    const arm = () => {
      const now = Date.now();
      const wakeAt = [nextUtcMidnightAfter(now), now + MAX_WAIT_MS];
      for (const iso of dates) {
        const ms = utcDayStartMs(iso);
        if (ms !== null && ms > now) wakeAt.push(ms);
      }
      if (expiredSinceFetch(dates, now, fetchedAtMs) !== null) wakeAt.push(now + RETRY_MS);
      const wait = Math.min(...wakeAt) - now;
      timer = setTimeout(() => {
        setNowMs(Date.now());
        arm();
      }, wait);
    };
    arm();
    return () => clearTimeout(timer);
  }, [dateKey, fetchedAtMs]);

  return nowMs;
}
