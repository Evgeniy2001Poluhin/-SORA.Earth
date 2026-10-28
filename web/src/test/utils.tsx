import type { ReactElement, ReactNode } from "react";
import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { auth } from "@/api/client";
import type { UserInfo } from "@/api/types";
import { useAuth } from "@/store/auth";
import { session } from "@/lib/session";
import { Providers } from "@/app/providers";
import { createAppQueryClient } from "@/app/queryClient";

/**
 * A query client with retries and caching disabled so tests observe exactly one
 * deterministic attempt per query.
 */
export function makeTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
}

export function renderWithQuery(
  ui: ReactElement,
  options: RenderOptions & { queryClient?: QueryClient } = {},
): RenderResult & { queryClient: QueryClient } {
  const { queryClient = makeTestQueryClient(), ...renderOptions } = options;
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { ...render(ui, { wrapper, ...renderOptions }), queryClient };
}

/**
 * A signed-in session without the network: the token in the transport and
 * sessionStorage, the account in the store. For tests about a page, not about
 * signing in -- those go through `useAuth.getState().login` with fetch stubs.
 */
export function seedSession(user: UserInfo, token: string): void {
  auth.set(token);
  try { sessionStorage.setItem("sora.token", token); } catch { /* storage unavailable */ }
  useAuth.setState({ phase: "ready", epoch: session.epoch(), user, token, notice: null, redirect: null });
}

export const SPECIALIST_A: UserInfo = { user_id: "11111111-1111-4111-8111-111111111111", username: "spec-a", role: "specialist" };
export const SPECIALIST_B: UserInfo = { user_id: "22222222-2222-4222-8222-222222222222", username: "spec-b", role: "specialist" };
export const ANALYST: UserInfo = { user_id: "33333333-3333-4333-8333-333333333333", username: "ana", role: "analyst" };
export const ADMIN: UserInfo = { user_id: "44444444-4444-4444-8444-444444444444", username: "root", role: "admin" };

/** A promise the test settles by hand, to hold a response in flight. */
export function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

export const jsonResponse = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });

type Handler = (call: { url: string; method: string; init: RequestInit }) => Response | Promise<Response>;

/**
 * Answer fetch by method and path. Unmatched requests answer `{}` with 200 only
 * if `fallback` says so; otherwise they fail the test loudly.
 */
export function routeFetch(routes: Array<[method: string, path: string | RegExp, handler: Handler]>, fallback?: Handler) {
  const stub = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = String(init.method ?? "GET").toUpperCase();
    for (const [m, p, h] of routes) {
      const hit = typeof p === "string" ? url === p : p.test(url);
      if (m === method && hit) return h({ url, method, init });
    }
    if (fallback) return fallback({ url, method, init });
    throw new Error("Unexpected request in test: " + method + " " + url);
  });
  globalThis.fetch = stub as unknown as typeof fetch;
  return stub;
}

/**
 * The application as it runs: the real Providers (QueryClient settings, session
 * boundary, toaster) and the real routes, under a MemoryRouter.
 */
export function renderApp(path: string, ui: ReactElement, queryClient: QueryClient = createAppQueryClient()) {
  const result = render(
    <Providers queryClient={queryClient} router={MemoryRouter} initialEntries={[path]}>{ui}</Providers>,
  );
  return { ...result, queryClient };
}

/** Recharts needs a non-zero layout box, which jsdom does not provide. */
export function stubChartLayout(width = 800, height = 400): void {
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: width });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: height });
  Object.defineProperty(HTMLElement.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => ({ width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0, toJSON: () => ({}) }),
  });
}
