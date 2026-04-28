import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "@/store/auth";

export function LoginPage() {
  const [u, setU] = useState("admin");
  const [p, setP] = useState("sora2026");
  const login = useAuth((s) => s.login);
  const loading = useAuth((s) => s.loading);
  const nav = useNavigate();

  return (
    <div className="card-body" style={{ padding: 32, maxWidth: 420, margin: "0 auto" }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>Authentication</div>
      <h1 className="display" style={{ fontSize: 32, margin: "0 0 24px" }}>Sign in</h1>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          try { await login(u, p); toast.success("Welcome, " + u); nav("/mlops"); }
          catch { toast.error("Invalid credentials"); }
        }}
        style={{ display: "grid", gap: 12 }}
      >
        <label style={{ display: "grid", gap: 4 }}>
          <span style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Username</span>
          <input className="preset-btn" value={u} onChange={(e) => setU(e.target.value)} style={{ padding: 10, background: "var(--bg)", color: "var(--text)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8 }} />
        </label>
        <label style={{ display: "grid", gap: 4 }}>
          <span style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>Password</span>
          <input type="password" value={p} onChange={(e) => setP(e.target.value)} style={{ padding: 10, background: "var(--bg)", color: "var(--text)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8 }} />
        </label>
        <button type="submit" disabled={loading} className="preset-btn" style={{ marginTop: 8, padding: 12, background: "#2FE0A6", color: "#0a0a0a", fontWeight: 600, border: "none", borderRadius: 8 }}>
          {loading ? "Signing in..." : "Sign in"}
        </button>
        <p style={{ color: "var(--faint)", fontSize: 11, marginTop: 8 }}>Default: admin / sora2026</p>
      </form>
    </div>
  );
}
