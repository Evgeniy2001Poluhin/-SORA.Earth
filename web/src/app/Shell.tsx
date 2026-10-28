import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import OnboardingTour from "./OnboardingTour";
import { useTheme } from "./theme-context";
import { ModelBadge } from "../components/ModelBadge";
import { HealthPill } from "../features/evaluate/HealthPill";
import { useServiceSnapshot } from "../features/evaluate/useServiceHealth";
import { navFor } from "./access";
import { useAuth } from "@/store/auth";
import "./shell.css";

export function Shell() {
  const { theme, toggle } = useTheme();
  const [tour, setTour] = useState(0);
  const navRef = useRef<HTMLElement>(null);
  const { pathname, search } = useLocation();
  // The header pill reads the service's own answer, the same shared query the
  // Evaluate hero uses (one request, not one per page).
  const { status } = useServiceSnapshot();
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const navigate = useNavigate();
  // The menu and the route guard read the same rule (./access.ts).
  const nav = navFor(user);

  // Bring the active link inside the nav's own scroll box, on load and on each
  // route change. scrollLeft is set directly: scrollIntoView would also scroll
  // ancestors such as .shell, which is overflow-x: hidden. Nothing moves when
  // the link is already visible.
  useEffect(() => {
    const nav = navRef.current;
    const active = nav?.querySelector<HTMLElement>("a.active");
    if (!nav || !active) return;
    const left = active.offsetLeft;
    const right = left + active.offsetWidth;
    if (left < nav.scrollLeft) nav.scrollLeft = left;
    else if (right > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = right - nav.clientWidth;
  }, [pathname]);

  return (
    <div className="shell">
      <header className="shell-top">
        <div className="shell-brand">
          <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden>
            <defs><radialGradient id="b" cx="35%" cy="35%" r="70%">
              <stop offset="0%" stopColor="#2FE0A6"/><stop offset="60%" stopColor="#15B887"/><stop offset="100%" stopColor="#062a20"/>
            </radialGradient></defs>
            <circle cx="16" cy="16" r="14" fill="url(#b)"/>
          </svg>
          <span className="display" style={{fontSize:20}}>SORA<span style={{color:"var(--planet)"}}>.</span>earth</span>
        </div>
        <nav className="shell-nav" ref={navRef}>
          {nav.map(n => <NavLink key={n.to} to={n.to} end data-tour={n.to.replace("/","")||"home"} className={({isActive})=>isActive?"active":""}>{n.label}</NavLink>)}
        </nav>
        <div className="shell-right" style={{display:"flex",alignItems:"center",gap:10}}>
          <button onClick={toggle} className="theme-toggle" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
            {theme === "dark" ? "☀️" : "🌙"}
          </button>
          <button onClick={()=>setTour(t=>t+1)} className="theme-toggle" title="Take the tour" aria-label="Take the tour">?</button>
          <ModelBadge />
          <HealthPill status={status} />
          {user ? (
            <div className="shell-account" style={{display:"flex",alignItems:"center",gap:8,fontSize:12}}>
              <span className="shell-user" title={"Signed in as " + user.username}>{user.username} · {user.role}</span>
              <button type="button" className="theme-toggle" style={{width:"auto",padding:"0 10px"}}
                onClick={() => navigate("/login?next=" + encodeURIComponent(pathname + search))}>Switch account</button>
              <button type="button" className="theme-toggle" style={{width:"auto",padding:"0 10px"}}
                onClick={() => void logout()}>Sign out</button>
            </div>
          ) : (
            pathname !== "/login" && <Link to={"/login?next=" + encodeURIComponent(pathname + search)} className="shell-signin" style={{fontSize:12,color:"var(--planet)"}}>Sign in</Link>
          )}
        </div>
      </header>
      <main className="shell-main"><Outlet/></main>
      <OnboardingTour runSignal={tour} />
    </div>
  );
}
