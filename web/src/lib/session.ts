import type { QueryClient } from "@tanstack/react-query";

/**
 * The browser session's generation, and everything that has to end with it.
 *
 * Every sign-in, sign-out, account switch and expiry advances the epoch. A
 * request remembers the epoch it started in; a response that arrives in a later
 * one belongs to somebody who is no longer here and is refused before anything
 * reads it -- `fetch` can resolve after `abort()`, and a JSON body or Blob can
 * finish reading after the account changed.
 *
 * This module imports nothing from the transport or the store, so both can
 * import it without a cycle: the transport reports an expired session here, and
 * the store registers what to do about it.
 */

let epoch = 0;
let controller = new AbortController();
let expiredHandler: ((epoch: number) => void) | null = null;
let queryClient: QueryClient | null = null;

/** Thrown in place of a response that outlived the session that asked for it. */
export class SessionChangedError extends Error {
  constructor() {
    super("The session changed before the response arrived");
    this.name = "SessionChangedError";
  }
}

/** True for an error that only says the session ended or a request was aborted:
 *  nothing to report to the user, nothing to retry. */
export function isSessionEnded(error: unknown): boolean {
  if (error instanceof SessionChangedError) return true;
  return !!error && typeof error === "object" && (error as { name?: unknown }).name === "AbortError";
}

export const session = {
  /** The current generation. */
  epoch(): number {
    return epoch;
  },
  /** Aborted when the current generation ends. */
  signal(): AbortSignal {
    return controller.signal;
  },
  isCurrent(e: number): boolean {
    return e === epoch;
  },
  /** Throws SessionChangedError unless `e` is still the current generation. */
  assertCurrent(e: number): void {
    if (e !== epoch) throw new SessionChangedError();
  },
  /** End the current generation: abort everything it started, and begin the next. */
  advance(): number {
    const old = controller;
    controller = new AbortController();
    epoch += 1;
    old.abort();
    return epoch;
  },
  /** The transport calls this for a 401 answered to the current session's Bearer. */
  reportExpired(e: number): void {
    if (e === epoch && expiredHandler) expiredHandler(e);
  },
  setExpiredHandler(fn: ((epoch: number) => void) | null): void {
    expiredHandler = fn;
  },
  /** The QueryClient the application renders with, so a transition can empty it. */
  bindQueryClient(qc: QueryClient | null): void {
    queryClient = qc;
  },
  queryClient(): QueryClient | null {
    return queryClient;
  },
};

/** One signal that aborts when either does. */
export function anySignal(...signals: (AbortSignal | null | undefined)[]): AbortSignal {
  const present = signals.filter((s): s is AbortSignal => !!s);
  if (present.length === 1) return present[0];
  const c = new AbortController();
  for (const s of present) {
    if (s.aborted) {
      c.abort(s.reason);
      break;
    }
    s.addEventListener("abort", () => c.abort(s.reason), { once: true });
  }
  return c.signal;
}
