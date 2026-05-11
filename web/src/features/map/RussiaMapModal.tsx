import { useEffect, useMemo, useState } from "react";
import RussiaMap, { type MapMode } from "./RussiaMap";
import { useRussiaMap } from "@/hooks/useRussiaMap";
import { RUSSIA_REGIONS, FD_COLORS, type RussianRegion } from "@/data/russia_regions";

type FD = RussianRegion["district"];
const ALL_FD: FD[] = ["ЦФО", "СЗФО", "ЮФО", "СКФО", "ПФО", "УФО", "СФО", "ДФО"];

const btnBase = {
  background: "transparent",
  border: "1px solid #2a2e33",
  color: "#cfcfcf",
  borderRadius: 6,
  padding: "4px 10px",
  cursor: "pointer",
  fontSize: 12,
} as const;

export default function RussiaMapModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [activeFD, setActiveFD] = useState<Set<FD>>(new Set(ALL_FD));
  const [search, setSearch] = useState("");
  const [mode, setMode] = useState<MapMode>("population");
  const [selected, setSelected] = useState<RussianRegion | null>(null);

  const { data: apiRegions } = useRussiaMap();
  const enrichedRegions = useMemo(() => {
    if (!apiRegions?.length) return RUSSIA_REGIONS;
    const esgMap = new Map(apiRegions.map(r => [r.code, r.esg.score]));
    return RUSSIA_REGIONS.map(r => ({ ...r, esgScore: esgMap.get(r.code) ?? r.esgScore }));
  }, [apiRegions]);

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

  const toggleFD = (fd: FD) => {
    setActiveFD(prev => {
      const next = new Set(prev);
      if (next.has(fd)) next.delete(fd);
      else next.add(fd);
      return next;
    });
  };

  const stats = useMemo(() => {
    const v = enrichedRegions.filter(r => activeFD.has(r.district));
    return { count: v.length, pop: v.reduce((s, r) => s + r.population, 0) };
  }, [activeFD, enrichedRegions]);

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
        onClick={e => e.stopPropagation()}
        style={{
          width: "min(1400px,96vw)",
          height: "min(860px,92vh)",
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
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            padding: "12px 20px",
            borderBottom: "1px solid #1f2225",
          }}
        >
          <div>
            <div style={{ fontSize: 16, fontWeight: 600, color: "#e6e6e6" }}>
              Карта России
            </div>
            <div style={{ fontSize: 12, opacity: 0.6, color: "#cfcfcf" }}>
              {stats.count} субъектов - {(stats.pop / 1e6).toFixed(1)}M чел.
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              placeholder="Поиск региона или столицы"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                background: "#0f1214",
                border: "1px solid #2a2e33",
                color: "#e6e6e6",
                borderRadius: 6,
                padding: "6px 10px",
                fontSize: 12,
                width: 240,
              }}
            />
            <div style={{ display: "flex", border: "1px solid #2a2e33", borderRadius: 6, overflow: "hidden" }}>
              {(["population", "esg"] as MapMode[]).map(m => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  style={{
                    ...btnBase,
                    border: "none",
                    borderRadius: 0,
                    background: mode === m ? "#1a1d20" : "transparent",
                    color: mode === m ? "#e6e6e6" : "#8a8f96",
                  }}
                >
                  {m === "population" ? "Население" : "ESG"}
                </button>
              ))}
            </div>
            <button onClick={onClose} style={btnBase}>Esc</button>
          </div>
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
          {ALL_FD.map(fd => {
            const active = activeFD.has(fd);
            return (
              <button
                key={fd}
                onClick={() => toggleFD(fd)}
                style={{
                  ...btnBase,
                  opacity: active ? 1 : 0.4,
         display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: "50%",
                    background: FD_COLORS[fd],
                    border: "1px solid #fff",
                  }}
                />
                {fd}
              </button>
            );
          })}
        </div>

        <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <RussiaMap
              height="100%"
              activeFD={activeFD}
              search={search}
              mode={mode}
              onSelect={setSelected}
              regions={enrichedRegions}
            />
          </div>
          {selected && (
            <aside
              style={{
                width: 320,
                borderLeft: "1px solid #1f2225",
                padding: 20,
                color: "#e6e6e6",
                fontSize: 13,
                overflow: "auto",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{selected.name}</div>
                <button onClick={() => setSelected(null)} style={btnBase}>x</button>
              </div>
              <div style={{ opacity: 0.7, marginBottom: 12 }}>Столица: {selected.capital}</div>
              <Row k="Код" v={selected.code} />
              <Row k="Округ" v={selected.district} color={FD_COLORS[selected.district]} />
              <Row k="Население" v={`${(selected.population / 1e6).toFixed(2)}M`} />
              <Row k="Широта" v={selected.lat.toFixed(4)} />
              <Row k="Долгота" v={selected.lon.toFixed(4)} />
              {selected.esgScore != null && (
                <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #1a1d20" }}>
                  <div style={{ fontSize: 11, opacity: 0.5, marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5 }}>
                    ESG Score
                  </div>
                  <Row k="Total" v={Number(selected.esgScore).toFixed(1)} />
                  {(selected as any).esgBreakdown && (
                    <>
                      <Row k="E (environmental)" v={Number((selected as any).esgBreakdown.e_score).toFixed(1)} color="#5A9A6F" />
                      <Row k="S (social)"        v={Number((selected as any).esgBreakdown.s_score).toFixed(1)} color="#8FB069" />
                      <Row k="G (governance)"    v={Number((selected as any).esgBreakdown.g_score).toFixed(1)} color="#C9A96E" />
                    </>
                  )}
                  {(selected as any).confidence != null && (
                    <Row
                      k="Confidence"
                      v={`${((selected as any).confidence * 100).toFixed(0)}%`}
                      color={
                        (selected as any).confidence >= 0.67 ? "#5A9A6F"
                        : (selected as any).confidence >= 0.34 ? "#C9A96E"
                        : "#B85C5C"
                      }
                    />
                  )}
                  {(selected as any).sourcesUsed?.length > 0 && (
                    <div style={{ marginTop: 8, fontSize: 11, opacity: 0.55 }}>
                      Sources: {(selected as any).sourcesUsed.join(", ")}
                    </div>
                  )}
                  {(selected as any).updatedAt && (
                    <div style={{ marginTop: 4, fontSize: 10, opacity: 0.4 }}>
                      Updated: {new Date((selected as any).updatedAt).toLocaleString("ru-RU")}
                    </div>
                  )}
                </div>
              )}
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ k, v, color }: { k: string; v: string; color?: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid #1a1d20" }}>
      <span style={{ opacity: 0.6 }}>{k}</span>
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {color && <span style={{ width: 8, height: 8, borderRadius: "50%", background: color }} />}
        {v}
      </span>
    </div>
  );
}
