import { NavLink, Outlet } from "react-router-dom";
import { useTheme } from "./ThemeProvider";
import { ModelBadge } from "../components/ModelBadge";
import "./shell.css";

const NAV = [ { to:"/", label:"Home" }, { to:"/evaluate", label:"Evaluate" }, { to:"/compare", label:"Compare" }, { to:"/drift", label:"Drift" }, { to:"/explain", label:"Explain" }, { to:"/copilot", label:"✨ Co-Pilot" }, { to:"/calibration", label:"Calibration" }, { to:"/mlops", label:"MLOps" }, { to:"/history", label:"History" }, { to:"/map", label:"Map" }, { to:"/forecast", label:"Forecast" }, { to:"/compliance", label:"Compliance" } ];

export function Shell() {
  const { theme, toggle } = useTheme();
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
        <nav className="shell-nav">
          {NAV.map(n => <NavLink key={n.to} to={n.to} end className={({isActive})=>isActive?"active":""}>{n.label}</NavLink>)}
        </nav>
        <div className="shell-right" style={{display:"flex",alignItems:"center",gap:10}}>
          <button onClick={toggle} className="theme-toggle" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
            {theme === "dark" ? "☀️" : "🌙"}
          </button>
          <ModelBadge />
          <span className="status-pill">Operational</span>
        </div>
      </header>
      <main className="shell-main"><Outlet/></main>
    </div>
  );
}
