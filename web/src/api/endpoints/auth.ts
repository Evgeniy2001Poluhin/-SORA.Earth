import { api } from "../client";
import type { Token, UserInfo, LoginRequest } from "../types";

export const authApi = {
  /** Sent without any Bearer: signing in as B must not carry A's token. */
  login: (b: LoginRequest) =>
    api<Token>("/auth/login-json", { method: "POST", body: JSON.stringify(b), bearer: null, cache: "no-store" }),
  /** With `bearer`, checks that token rather than the session's own. */
  me: (bearer?: string) =>
    api<UserInfo>("/auth/me", bearer === undefined ? { cache: "no-store" } : { bearer, cache: "no-store" }),
};
