import type { UserInfo } from "@/api/types";

/**
 * What each role may open, in one place: the menu and the route guard both
 * read it, so a page hidden from the menu is also refused at its URL, before
 * its component mounts or sends anything.
 *
 * The server is the control (app/auth.py refuse_specialist, app/project_access.py);
 * this only keeps the interface from offering what the server will refuse.
 */

/** Pages a specialist may not open: their storage has no owner column. */
const SPECIALIST_REFUSED = new Set(["/copilot"]);

export const NAV: ReadonlyArray<{ to: string; label: string }> = [
  { to: "/", label: "Home" },
  { to: "/evaluate", label: "Evaluate" },
  { to: "/compare", label: "Compare" },
  { to: "/drift", label: "Drift" },
  { to: "/explain", label: "Explain" },
  { to: "/copilot", label: "✨ Co-Pilot" },
  { to: "/calibration", label: "Calibration" },
  { to: "/mlops", label: "MLOps" },
  { to: "/history", label: "History" },
  { to: "/map", label: "Map" },
  { to: "/forecast", label: "Forecast" },
  { to: "/webhooks", label: "Webhooks" },
  { to: "/status", label: "Status" },
  { to: "/compliance", label: "Compliance" },
];

function firstSegment(path: string): string {
  const p = path.split(/[?#]/)[0];
  const seg = p.split("/").filter(Boolean)[0];
  return seg ? "/" + seg : "/";
}

/** May this user open this path? */
export function canOpen(path: string, user: UserInfo | null): boolean {
  if (user?.role === "specialist" && SPECIALIST_REFUSED.has(firstSegment(path))) return false;
  return true;
}

/** The menu for this user. */
export function navFor(user: UserInfo | null) {
  return NAV.filter((n) => canOpen(n.to, user));
}

export type SaveAccess = "anonymous" | "forbidden" | "allowed";

/** Who may save an evaluation: the same rule as app/project_access.py SAVING_ROLES. */
export function saveAccess(user: UserInfo | null): SaveAccess {
  if (!user) return "anonymous";
  return user.role === "specialist" || user.role === "admin" ? "allowed" : "forbidden";
}

/** What the history page holds for this user. */
export function historyTitle(user: UserInfo | null): string {
  if (!user) return "History";
  if (user.role === "specialist") return "My evaluations";
  if (user.role === "admin") return "All evaluations";
  return "Shared history";
}

/** A same-origin path to come back to, or null. Never another origin, never /login. */
export function safeReturnTo(raw: string | null | undefined): string | null {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return null;
  if (firstSegment(raw) === "/login") return null;
  return raw;
}

/** Where a user lands after signing in. */
export function landingPath(user: UserInfo, returnTo: string | null | undefined): string {
  if (user.role === "specialist") return "/history";
  const back = safeReturnTo(returnTo);
  if (back && canOpen(back, user)) return back;
  return user.role === "admin" ? "/mlops" : "/";
}
