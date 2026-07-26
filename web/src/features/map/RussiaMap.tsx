import { MapContainer, TileLayer, CircleMarker, Tooltip, GeoJSON, useMap } from "react-leaflet";
import { Path, type LatLngBoundsExpression, type Layer, type PathOptions } from "leaflet";
import type { Feature, FeatureCollection, Geometry } from "geojson";
import { useEffect, useState, useMemo } from "react";
import "leaflet/dist/leaflet.css";
import "./map.css";
import { RUSSIA_REGIONS, FD_COLORS, type RussianRegion, type EnrichedRussianRegion } from "@/data/russia_regions";

/** /geo/russia.geo.json keys each polygon by the same region code as RUSSIA_REGIONS. */
type RegionFeature = Feature<Geometry, { code: string }>;

const B: LatLngBoundsExpression = [[41, 19], [82, 185]];
const radByPop = (p: number) => 4 + Math.min(8, Math.log10(Math.max(p, 1000)) - 3);
const radByEsg = (s?: number) => 4 + ((s ?? 50) - 40) / 15;

const esgColor = (s?: number) => {
  if (s == null) return "#808890";
  if (s >= 80) return "#2FE0A6";
  if (s >= 70) return "#7BC678";
  if (s >= 60) return "#E4C04A";
  if (s >= 50) return "#E4954A";
  return "#E4504A";
};

function FitOnMount() {
  const map = useMap();
  useEffect(() => { map.fitBounds(B); }, [map]);
  return null;
}

const FD_FILL: Record<string, string> = {
  "ЦФО": "#7493C7", "СЗФО": "#6BC7C7", "ЮФО": "#74C7A9", "СКФО": "#C7B363",
  "ПФО": "#C7A05E", "УФО": "#C77979", "СФО": "#9782C7", "ДФО": "#C77DAD",
};

export type MapMode = "population" | "esg";

interface Props {
  height?: number | string;
  activeFD: Set<RussianRegion["district"]>;
  search: string;
  mode: MapMode;
  onSelect: (r: RussianRegion) => void;
  regions?: EnrichedRussianRegion[];
}

export default function RussiaMap({ height = 560, activeFD, search, mode, onSelect, regions }: Props) {
  const source: EnrichedRussianRegion[] = regions ?? RUSSIA_REGIONS;
  const q = search.trim().toLowerCase();
  const visible = source.filter(r =>
    activeFD.has(r.district) &&
    (q === "" || r.name.toLowerCase().includes(q) || r.capital.toLowerCase().includes(q))
  );

  const [geo, setGeo] = useState<FeatureCollection<Geometry, { code: string }> | null>(null);
  useEffect(() => {
    fetch("/geo/russia.geo.json").then(r => r.json()).then(setGeo).catch(() => setGeo(null));
  }, []);

  const byCode = useMemo(() => new Map(source.map(r => [r.code, r])), [source]);

  const geoStyle = (feat?: RegionFeature): PathOptions => {
    const r = feat && byCode.get(feat.properties.code);
    const on = r && activeFD.has(r.district) &&
      (q === "" || r.name.toLowerCase().includes(q) || r.capital.toLowerCase().includes(q));
    const fill = !r ? "#5A6068" : mode === "esg" ? esgColor(r.esgScore) : FD_FILL[r.district];
    return { fillColor: fill, color: "rgba(255,255,255,0.22)", weight: 0.75,
             fillOpacity: on ? 0.18 : 0.03, opacity: on ? 0.5 : 0.08 };
  };

  const onEachFeature = (feat: RegionFeature, layer: Layer) => {
    const r = byCode.get(feat.properties.code);
    // Only Path layers carry setStyle; GeoJSON polygons always are, but narrow
    // rather than assert so a non-path layer cannot throw at runtime.
    if (!r || !(layer instanceof Path)) return;
    layer.on({
      mouseover: () => layer.setStyle({ weight: 1.5, color: "#fff", fillOpacity: 0.32 }),
      mouseout:  () => layer.setStyle(geoStyle(feat)),
      click:     () => onSelect(r),
    });
  };

  return (
    <div className="map-wrap" style={{ height, position: "relative" }}>
      <MapContainer
        bounds={B}
        minZoom={3}
        maxZoom={10}
        scrollWheelZoom
        attributionControl={false}
        style={{ height: "100%", width: "100%", background: "var(--bg-1)" }}
      >
        <FitOnMount />
        <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
        {geo && (
          <GeoJSON key={`${mode}-${q}-${[...activeFD].join()}`} data={geo} style={geoStyle} onEachFeature={onEachFeature} />
        )}
        {visible.map(r => {
          const fill = mode === "esg" ? esgColor(r.esgScore) : FD_COLORS[r.district];
          const radius = mode === "esg" ? radByEsg(r.esgScore) : radByPop(r.population);
          return (
            <CircleMarker
              key={`${r.code}-${mode}`}
              center={[r.lat, r.lon]}
              radius={radius}
              pathOptions={{ fillColor: fill, color: "#fff", weight: 1.25, fillOpacity: 0.9 }}
              eventHandlers={{ click: () => onSelect(r) }}
            >
              <Tooltip direction="top" offset={[0, -6]} className="esg-tooltip">
                <strong>{r.capital}</strong>
                <div style={{ fontSize: 11, opacity: 0.8 }}>{r.name}</div>
                <div style={{ fontSize: 10, opacity: 0.6 }}>
                  {r.district} · {(r.population / 1e6).toFixed(2)}M
                  {r.esgScore != null && ` · ESG ${r.esgScore}`}
                </div>
                {r.esgBreakdown && (
                  <div style={{ fontSize: 10, opacity: 0.75, marginTop: 2, display: "flex", gap: 6 }}>
                    <span style={{ color: "#5A9A6F" }}>E{Math.round(r.esgBreakdown.e_score)}</span>
                    <span style={{ color: "#8FB069" }}>S{Math.round(r.esgBreakdown.s_score)}</span>
                    <span style={{ color: "#C9A96E" }}>G{Math.round(r.esgBreakdown.g_score)}</span>
                    {r.confidence != null && (
                      <span style={{ opacity: 0.55, marginLeft: 4 }}>
                        · conf {Math.round(r.confidence * 100)}%
                      </span>
                    )}
                  </div>
                )}
              </Tooltip>
            </CircleMarker>
          );
        })}
      </MapContainer>
      {mode === "esg" && (
        <div style={{
          position: "absolute", bottom: 12, right: 12, zIndex: 500,
          background: "rgba(11,13,15,0.85)", border: "1px solid #1f2225",
          borderRadius: 8, padding: "8px 10px", fontSize: 11, color: "#e5e7eb",
          backdropFilter: "blur(6px)", minWidth: 140,
        }}>
          <div style={{ opacity: 0.6, marginBottom: 4, fontSize: 10, textTransform: "uppercase", letterSpacing: 0.5 }}>ESG Score</div>
          <div style={{ height: 8, borderRadius: 4,
            background: "linear-gradient(90deg, #E4504A 0%, #E4954A 25%, #E4C04A 50%, #7BC678 75%, #2FE0A6 100%)",
            marginBottom: 4 }} />
          <div style={{ display: "flex", justifyContent: "space-between", opacity: 0.7, fontSize: 10 }}>
            <span>0</span><span>50</span><span>100</span>
          </div>
        </div>
      )}
    </div>
  );
}
