import { Fragment, useEffect, type PropsWithChildren } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/store/auth";

/**
 * Everything user-facing mounts inside this, keyed by the session epoch.
 *
 * While the stored token is being checked, or one session is ending and the
 * next is not yet published, nothing below is mounted: no page can render the
 * previous account's data for even one frame, and no protected request can
 * start before the account is known. When the next session is ready the whole
 * tree mounts fresh, so forms, results, snapshots, chats and secrets from the
 * previous one are gone with their components.
 */
export function SessionBoundary({ children }: PropsWithChildren) {
  const phase = useAuth((s) => s.phase);
  const epoch = useAuth((s) => s.epoch);
  const redirect = useAuth((s) => s.redirect);
  const notice = useAuth((s) => s.notice);
  const consumeRedirect = useAuth((s) => s.consumeRedirect);
  const navigate = useNavigate();
  const { pathname, search } = useLocation();

  useEffect(() => {
    if (phase === "ready" && redirect) {
      consumeRedirect();
      navigate(redirect, { replace: true });
    }
  }, [phase, redirect, consumeRedirect, navigate]);

  if (phase === "hydrating" || phase === "transitioning") {
    return (
      <div className="session-wait" role="status" style={waitStyle}>
        {phase === "hydrating" ? "Checking your session…" : "Switching session…"}
      </div>
    );
  }
  if (phase === "unverified") return <Unverified />;

  const here = pathname + search;
  return (
    <Fragment key={epoch}>
      {notice === "expired" && (
        <div className="session-notice" role="alert" style={noticeStyle}>
          Your session has expired. Sign in again.{" "}
          {pathname !== "/login" && (
            <Link to={"/login?next=" + encodeURIComponent(here)}>Sign in</Link>
          )}
        </div>
      )}
      {children}
    </Fragment>
  );
}

function Unverified() {
  const hydrate = useAuth((s) => s.hydrate);
  const logout = useAuth((s) => s.logout);
  return (
    <div className="session-wait" role="alert" style={waitStyle}>
      <p>Your session could not be checked: the service is unavailable.</p>
      <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
        <button type="button" onClick={() => void hydrate()}>Try again</button>
        <button type="button" onClick={() => void logout()}>Sign out</button>
      </div>
    </div>
  );
}

const waitStyle = {
  display: "grid", placeItems: "center", gap: 12, minHeight: 480,
  color: "var(--muted)", fontSize: 13, fontFamily: "var(--f-mono)",
} as const;

const noticeStyle = {
  padding: "10px 16px", background: "var(--bg-2)", color: "var(--text)",
  borderBottom: "1px solid var(--line)", fontSize: 13,
} as const;
