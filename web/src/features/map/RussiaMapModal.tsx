import { useEffect } from "react";
import RussiaMap from "./RussiaMap";
import { FD_COLORS } from "@/data/russia_regions";

export default function RussiaMapModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", k);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", k);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0,0,0,.72)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(1280px,96vw)",
          height: "min(820px,92vh)",
          background: "#0b0d0f",
          border: "1px solid #1f2225",
          borderRadius: 12,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <header
          style={{
            display: "flex",
            justifyContent: "space-between",
            padding: "14px 20px",
            borderBottom: "1px solid #1f2225",
          }}
        >
          <div>
            <div style={{ fontSize: 16, fontWeight: 600, color: "#e6e6e6" }}>
              Карта России
            </div>
            <div style={{ fontSize: 12, opacity: 0.6, color: "#cfcfcf" }}>
              85 субъектов РФ
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "1px solid #2a2e33",
              color: "#cfcfcf",
              borderRadius: 6,
              padding: "4px 12px",
              cursor: "pointer",
            }}
          >
            Esc
          </button>
        </header>
        <div
          style={{
            display: "flex",
            gap: 10,
            flexWrap: "wrap",
            padding: "8px 20px",
            borderBottom: "1px solid #1f2225",
          }}
        >
          {Object.entries(FD_COLORS).map(([fd, c]) => (
            <div
              key={fd}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 12,
                color: "#cfcfcf",
              }}
            >
              <span
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: "50%",
                  background: c,
                  border: "1px solid #fff",
                }}
              />
              {fd}
            </div>
          ))}
        </div>
        <div style={{ flex: 1, minHeight: 0 }}>
          <RussiaMap height="100%" />
        </div>
      </div>
    </div>
  );
}
