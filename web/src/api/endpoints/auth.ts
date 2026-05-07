import { api } from "../client";
import type { Token, UserInfo, LoginRequest } from "../types";

export const authApi = {
  login: (b: LoginRequest) =>
    api<Token>("/auth/login-json", { method: "POST", body: JSON.stringify(b) }),
  me: () => api<UserInfo>("/auth/me"),
  refresh: (refresh_token: string) =>
    api<Token>("/auth/refresh", { method: "POST", body: JSON.stringify({ refresh_token }) }),
};
