import { anySignal, session } from "@/lib/session";

const BASE = "/api/v1";
let token: string | null = null;
let apiKey: string | null = null;

export const auth = {
  set(t: string | null) { token = t; },
  get() { return token; },
  setApiKey(k: string | null) { apiKey = k; },
  getApiKey() { return apiKey; },
};

/**
 * A failed response, carrying the parts of it a caller can branch on (#250).
 *
 * `message` is byte-identical to the string this module has always thrown, so
 * every existing consumer -- `errorMessage(e)`, toasts, error boundaries --
 * sees exactly what it saw before. What is new is `status` and `reasonCode`,
 * which is what a domain branch should be selected by.
 *
 * The one place in the app that told two 4xx cases apart did it with
 * `msg.includes("fit baseline first")`, matching English prose from a
 * FastAPI handler. Rewording that sentence, adding a full stop or translating
 * it would have silently replaced a helpful hint with a generic failure, and
 * nothing would have gone red. Measured before fixing: across the non-test
 * frontend there are nine `.includes`/`.startsWith`/`.match`/`.indexOf` calls
 * and exactly one of them reads a server error's text.
 */
export class ApiError extends Error {
  readonly status: number;
  /** The parsed JSON body, or the raw text when it is not JSON. */
  readonly body: unknown;
  /** A machine-readable branch selector, when the handler supplies one. */
  readonly reasonCode: string | null;

  constructor(status: number, message: string, body: unknown, reasonCode: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.reasonCode = reasonCode;
  }
}

/**
 * Pull `reason_code` out of an error body, whichever way the handler shaped it.
 *
 * FastAPI produces two: `HTTPException(400, detail={...})` nests the payload
 * under `detail`, while a handler returning `JSONResponse` puts its model at
 * the top level. Both are in this codebase, so both are read. Anything else
 * yields `null` rather than a guess -- a wrong branch selector is worse than
 * no branch selector, because it looks like it worked.
 */
function reasonCodeOf(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const top = (body as { reason_code?: unknown }).reason_code;
  if (typeof top === "string" && top) return top;
  const detail = (body as { detail?: unknown }).detail;
  if (detail && typeof detail === "object") {
    const nested = (detail as { reason_code?: unknown }).reason_code;
    if (typeof nested === "string" && nested) return nested;
  }
  return null;
}

/**
 * One request through the shared transport.
 *
 * Everything that sends a caller's data goes through here -- JSON, Blob, the
 * Co-Pilot stream and empty answers -- so every one of them carries the Bearer,
 * is aborted when the session ends, and is refused if it answers after the
 * session changed.
 *
 * `bearer` overrides the session's token for this request: `null` sends none
 * (the login call), a string sends that one (checking a candidate token with
 * /auth/me before it becomes the application's).
 *
 * A 401 answered to the current session's own Bearer ends the session, once.
 * Nothing is refreshed, and nothing is replayed without the token: on routes
 * with an anonymous path, dropping the Bearer changes where a private payload
 * goes (shared caches and telemetry), which is not what the user asked for.
 */
export interface ApiInit extends RequestInit {
  bearer?: string | null;
}

interface Sent {
  res: Response;
  epoch: number;
}

async function send(path: string, init: ApiInit = {}): Promise<Sent> {
  const { bearer, signal, ...rest } = init;
  const epoch = session.epoch();
  const used = bearer !== undefined ? bearer : token;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((rest.headers as Record<string, string> | undefined) || {}),
  };
  if (used) headers["Authorization"] = "Bearer " + used;
  if (apiKey) headers["X-API-Key"] = apiKey;

  const request: RequestInit = { ...rest, headers, signal: anySignal(signal, session.signal()) };
  // A signed-in answer is private: never from, or into, the browser's HTTP cache.
  if (used && request.cache === undefined) request.cache = "no-store";

  const res = await fetch(BASE + path, request);
  session.assertCurrent(epoch);
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    let body: unknown = t;
    try {
      body = JSON.parse(t);
    } catch {
      /* not JSON: leave the raw text, and let reasonCodeOf answer null */
    }
    session.assertCurrent(epoch);
    // Only the session's own token: a 401 to a candidate token being checked at
    // sign-in, or to a request from a session that has since ended, ends nothing.
    if (res.status === 401 && used && used === token) session.reportExpired(epoch);
    // The message is unchanged on purpose. Widening the thrown type is safe
    // for every existing consumer only as long as what they read stays the same.
    throw new ApiError(
      res.status,
      "API " + res.status + ": " + (t || res.statusText),
      body,
      reasonCodeOf(body),
    );
  }
  return { res, epoch };
}

/** A JSON answer with its status, for callers that must tell 200 from 201. */
export async function apiWithStatus<T>(path: string, init: ApiInit = {}): Promise<{ status: number; data: T }> {
  const { res, epoch } = await send(path, init);
  const data = (res.status === 204 ? undefined : await res.json()) as T;
  session.assertCurrent(epoch);
  return { status: res.status, data };
}

export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  return (await apiWithStatus<T>(path, init)).data;
}

/** A binary answer (CSV, PDF, PNG), refused if the session changed while it was read. */
export async function apiBlob(path: string, init: ApiInit = {}): Promise<Blob> {
  const { res, epoch } = await send(path, init);
  const blob = await res.blob();
  session.assertCurrent(epoch);
  return blob;
}

/**
 * A streamed answer. The caller reads the body and checks
 * `session.isCurrent(epoch)` before acting on each chunk.
 */
export async function apiStream(path: string, init: ApiInit = {}): Promise<Sent> {
  return send(path, init);
}