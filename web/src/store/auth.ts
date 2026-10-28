import { create } from "zustand";
import { toast } from "sonner";
import { authApi } from "@/api/endpoints/auth";
import { ApiError, auth as client } from "@/api/client";
import type { UserInfo } from "@/api/types";
import { session } from "@/lib/session";
import { landingPath } from "@/app/access";

const TOKEN_KEY = "sora.token";

/**
 * hydrating     the stored token is being checked; nothing user-facing is mounted
 * transitioning one session is ending and the next is not yet published
 * unverified    a stored token could not be checked (service unavailable):
 *               neither signed in nor, silently, anonymous
 * ready         `user` is the session, or null for anonymous
 */
export type SessionPhase = "hydrating" | "transitioning" | "unverified" | "ready";

/** Why the last session ended, when the user should be told. */
export type SessionNotice = "expired" | null;

interface AuthState {
  phase: SessionPhase;
  /** Remount key for everything user-facing; equals session.epoch() once ready. */
  epoch: number;
  token: string | null;
  user: UserInfo | null;
  notice: SessionNotice;
  /** Where to go once the new session is mounted; SessionBoundary consumes it. */
  redirect: string | null;
  login: (u: string, p: string, returnTo?: string | null) => Promise<UserInfo>;
  logout: () => Promise<void>;
  hydrate: () => Promise<void>;
  /** Called by the transport for a 401 to the session's own token. */
  expire: (epoch: number) => void;
  consumeRedirect: () => void;
}

function readStoredToken(): string | null {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}
function storeToken(t: string | null): void {
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable */ }
}

let hydrateStarted = false;

export const useAuth = create<AuthState>((set, get) => {
  /**
   * The one way a session ends, whatever starts the next one.
   *
   * 1. Synchronously: advance the epoch (aborting every request the old one
   *    started, mutations, Blobs and streams included), drop the credentials,
   *    and close the screen -- `transitioning` unmounts everything user-facing.
   * 2. Cancel, then clear, both query and mutation caches. `clear()` does not
   *    cancel a pending mutation; the epoch check in the transport refuses its
   *    answer instead.
   * The caller then either publishes a new session or settles anonymous; either
   * way the tree mounts again under the new epoch, so no form, result, snapshot,
   * chat or secret from before survives.
   */
  const endSession = async (notice: SessionNotice): Promise<number> => {
    const e = session.advance();
    client.set(null);
    storeToken(null);
    set({ phase: "transitioning", epoch: e, token: null, user: null, notice, redirect: null });
    toast.dismiss();
    const qc = session.queryClient();
    if (qc) {
      await qc.cancelQueries();
      qc.clear();
    }
    return e;
  };

  return {
    phase: "hydrating",
    epoch: session.epoch(),
    token: null,
    user: null,
    notice: null,
    redirect: null,

    login: async (u, p, returnTo) => {
      const e = await endSession(null);
      try {
        session.assertCurrent(e);
        const t = await authApi.login({ username: u, password: p });
        session.assertCurrent(e);
        // The candidate token checks itself; it is not the application's token
        // until /auth/me has said whose it is.
        const me = await authApi.me(t.access_token);
        session.assertCurrent(e);
        client.set(t.access_token);
        storeToken(t.access_token);
        set({ phase: "ready", token: t.access_token, user: me, notice: null,
              redirect: landingPath(me, returnTo) });
        return me;
      } catch (err) {
        // A newer transition owns the state now; this attempt changes nothing.
        if (session.isCurrent(e)) set({ phase: "ready" });
        throw err;
      }
    },

    logout: async () => {
      const e = await endSession(null);
      if (session.isCurrent(e)) set({ phase: "ready" });
    },

    hydrate: async () => {
      if (hydrateStarted && get().phase !== "unverified") return;
      hydrateStarted = true;
      const e = session.epoch();
      const tok = readStoredToken();
      if (!tok) {
        set({ phase: "ready" });
        return;
      }
      set({ phase: "hydrating" });
      try {
        const me = await authApi.me(tok);
        if (!session.isCurrent(e)) return;
        client.set(tok);
        set({ phase: "ready", token: tok, user: me });
      } catch (err) {
        if (!session.isCurrent(e)) return;
        if (err instanceof ApiError && err.status === 401) {
          // The token is no longer valid: it goes, everywhere.
          storeToken(null);
          set({ phase: "ready", notice: "expired" });
        } else {
          // Not known to be invalid, and not known to be valid. Neither sign
          // the user in nor quietly continue as anonymous.
          set({ phase: "unverified" });
        }
      }
    },

    expire: (e) => {
      if (!session.isCurrent(e) || !get().user) return;
      void endSession("expired").then((next) => {
        if (session.isCurrent(next)) set({ phase: "ready" });
      });
    },

    consumeRedirect: () => set({ redirect: null }),
  };
});

session.setExpiredHandler((e) => useAuth.getState().expire(e));

/** Test support: back to a fresh page load, with nothing stored. */
export function resetAuthForTests(phase: SessionPhase = "ready"): void {
  session.advance();
  client.set(null);
  storeToken(null);
  hydrateStarted = false;
  useAuth.setState({ phase, epoch: session.epoch(), token: null, user: null, notice: null, redirect: null });
}
