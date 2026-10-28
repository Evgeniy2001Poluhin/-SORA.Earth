import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "@/store/auth";
import { ApiError } from "@/api/client";
import { isSessionEnded } from "@/lib/session";
import { errorMessage } from "@/lib/errors";

/**
 * Sign in, or switch account. Either way the previous session ends first
 * (store/auth.ts): this page unmounts with it, and the store sends the new
 * session where it belongs once /auth/me has said who it is.
 */
export function LoginPage() {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [params] = useSearchParams();
  const login = useAuth((s) => s.login);
  const current = useAuth((s) => s.user);
  const next = params.get("next");

  return (
    <div className="card-body" style={{ padding: 32, maxWidth: 420, margin: "0 auto" }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>Authentication</div>
      <h1 className="display" style={{ fontSize: 32, margin: "0 0 24px" }}>
        {current ? "Switch account" : "Sign in"}
      </h1>
      {current && (
        <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 16 }}>
          Signed in as {current.username}. Signing in as another account signs this one out first.
        </p>
      )}
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try {
            const me = await login(u, p, next);
            toast.success("Welcome, " + me.username);
          } catch (err) {
            if (isSessionEnded(err)) return;
            toast.error(err instanceof ApiError && err.status === 401
              ? "Invalid credentials"
              : "Sign-in failed: " + errorMessage(err));
          }
        }}
        style={{ display: "grid", gap: 12 }}
      >
        <label style={{ display: "grid", gap: 4 }}>
          <span style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Username</span>
          <input autoComplete="username" className="preset-btn" value={u} onChange={(e) => setU(e.target.value)} style={{ padding: 10, background: "var(--bg)", color: "var(--text)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8 }} />
        </label>
        <label style={{ display: "grid", gap: 4 }}>
          <span style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Password</span>
          <input type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} style={{ padding: 10, background: "var(--bg)", color: "var(--text)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8 }} />
        </label>
        <button type="submit" disabled={!u || !p} className="preset-btn" style={{ marginTop: 8, padding: 12, background: "#2FE0A6", color: "var(--bg-2)", fontWeight: 600, border: "none", borderRadius: 8 }}>
          Sign in
        </button>
      </form>
    </div>
  );
}
