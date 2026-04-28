import { create } from "zustand";
import { authApi } from "@/api/endpoints/auth";
import { auth as client } from "@/api/client";
import type { UserInfo } from "@/api/types";

const TOKEN_KEY = "sora.token";

interface AuthState {
  token: string | null;
  user: UserInfo | null;
  loading: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => void;
  hydrate: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  token: null,
  user: null,
  loading: false,
  login: async (u, p) => {
    set({ loading: true });
    try {
      const t = await authApi.login({ username: u, password: p });
      client.set(t.access_token);
      try { sessionStorage.setItem(TOKEN_KEY, t.access_token); } catch (e) { /* ignore */ }
      const me = await authApi.me();
      set({ token: t.access_token, user: me, loading: false });
    } catch (e) {
      set({ loading: false });
      throw e;
    }
  },
  logout: () => {
    client.set(null);
    try { sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* ignore */ }
    set({ token: null, user: null });
  },
  hydrate: async () => {
    let tok: string | null = null;
    try { tok = sessionStorage.getItem(TOKEN_KEY); } catch (e) { tok = null; }
    if (!tok) return;
    client.set(tok);
    try {
      const me = await authApi.me();
      set({ token: tok, user: me });
    } catch (e) {
      client.set(null);
      try { sessionStorage.removeItem(TOKEN_KEY); } catch (_e) { /* ignore */ }
    }
  },
}));