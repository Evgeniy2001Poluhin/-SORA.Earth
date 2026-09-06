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

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string> | undefined) || {}),
  };
  if (token) headers["Authorization"] = "Bearer " + token;
  if (apiKey) headers["X-API-Key"] = apiKey;

  const res = await fetch(BASE + path, { ...init, headers });
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    let body: unknown = t;
    try {
      body = JSON.parse(t);
    } catch {
      /* not JSON: leave the raw text, and let reasonCodeOf answer null */
    }
    // The message is unchanged on purpose. Widening the thrown type is safe
    // for every existing consumer only as long as what they read stays the same.
    throw new ApiError(
      res.status,
      "API " + res.status + ": " + (t || res.statusText),
      body,
      reasonCodeOf(body),
    );
  }
  return res.json() as Promise<T>;
}