import type { TooltipRenderProps } from "react-joyride";

const ICONS: Record<number, React.ReactNode> = {
  0: <path d="M3 12h18M12 3v18M5 6c4 3 10 3 14 0M5 18c4-3 10-3 14 0" />,
  1: <path d="M4 19V5M4 19h16M8 19v-6M12 19v-9M16 19v-4" />,
  2: <path d="M8 4h8v4H8zM6 8h12v10H6zM9 12h.01M15 12h.01M12 2v2" />,
  3: <path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2zM9 4v14M15 6v14" />,
  4: <path d="M4 12l5 5L20 6" />,
};

export default function TourTooltip({
  index, size, step, backProps, primaryProps, skipProps, tooltipProps, isLastStep,
}: TooltipRenderProps) {
  const pct = ((index + 1) / size) * 100;
  const card: React.CSSProperties = {
    width: 412,
    background: "#0a120f",
    border: "1px solid rgba(255,255,255,0.10)",
    borderRadius: 2,
    boxShadow: "0 40px 90px rgba(0,0,0,0.7)",
    overflow: "hidden",
    fontFamily: "inherit",
  };
  const head: React.CSSProperties = {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "16px 20px", borderBottom: "1px solid rgba(255,255,255,0.08)",
  };
  const iconBox: React.CSSProperties = {
    width: 30, height: 30, display: "grid", placeItems: "center",
    border: "1px solid #15B887", color: "#15B887",
  };
  const next: React.CSSProperties = {
    background: "#15B887", color: "#04130d", fontWeight: 700, fontSize: 12,
    letterSpacing: "0.08em", textTransform: "uppercase", border: "none",
    borderRadius: 0, padding: "11px 22px", cursor: "pointer",
  };
  const Svg = ({ d }: { d: React.ReactNode }) => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.6" strokeLinecap="square">{d}</svg>
  );
  return (
    <div {...tooltipProps} style={card}>
      <div style={{ height: 2, background: "rgba(255,255,255,0.07)" }}>
        <div style={{ height: 2, width: pct + "%", background: "#15B887", transition: "width .3s" }} />
      </div>
      <div style={head}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={iconBox}><Svg d={ICONS[index]} /></div>
          <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: "0.16em", textTransform: "uppercase", color: "#6f8980" }}>
            {String(index + 1).padStart(2, "0")} / {String(size).padStart(2, "0")}
          </span>
        </div>
        <button {...skipProps} style={{ background: "none", border: "none", color: "#5d706a", fontSize: 17, cursor: "pointer", lineHeight: 1 }}>&times;</button>
      </div>
      <div style={{ padding: "22px 24px 10px" }}>
        {step.title && (
          <div style={{ fontSize: 20, fontWeight: 800, color: "#fff", letterSpacing: "-0.01em", textTransform: "uppercase", marginBottom: 10, lineHeight: 1.1 }}>{step.title}</div>
        )}
        <div style={{ fontSize: 13.5, lineHeight: 1.65, color: "#9fb4ac" }}>{step.content}</div>
      </div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 24px 20px", marginTop: 8 }}>
        <div style={{ display: "flex", gap: 5 }}>
          {Array.from({ length: size }).map((_, i) => (
            <span key={i} style={{ width: i === index ? 20 : 7, height: 3, transition: "all .3s", background: i === index ? "#15B887" : "rgba(255,255,255,0.16)" }} />
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          {index > 0 && (
            <button {...backProps} style={{ background: "none", border: "none", color: "#9fb4ac", fontSize: 11.5, fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase", cursor: "pointer" }}>Back</button>
          )}
          <button {...primaryProps} style={next}>{isLastStep ? "Get started" : "Next"}</button>
        </div>
      </div>
    </div>
  );
}
