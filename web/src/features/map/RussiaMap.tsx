import { MapContainer, TileLayer, CircleMarker, Tooltip, useMap } from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import { useEffect } from "react";
import "leaflet/dist/leaflet.css";
import "./map.css";
import { RUSSIA_REGIONS, FD_COLORS, type RussianRegion } from "@/data/russia_regions";

const B: LatLngBoundsExpression = [[41, 19], [82, 185]];
const radByPop = (p: number) => 4 + Math.min(8, Math.log10(Math.max(p, 1000)) - 3);
const radByEsg = (s?: number) => 4 + ((s ?? 50) - 40) / 15;

const esgColor = (s?: number) => {
  if (s == null) return "#808890";
  if (s >= 80) return "#5A9A6F";
  if (s >= 70) return "#8FB069";
  if (s >= 60) return "#C9A96E";
  if (s >= 50) return "#C97D4E";
  return "#B85C5C";
};

function FitOnMount() {
  const map = useMap();
  useEffect(() => { map.fitBounds(B); }, [map]);
  return null;
}

export type MapMode = "population" | "esg";

interface Props {
  height?: number | string;
  activeFD: Set<RussianRegion["district"]>;
  search: string;
  mode: MapMode;
  onSelect: (r: RussianRegion) => void;
  regions?: RussianRegion[];
}

export default function RussiaMap({ height = 560, activeFD, search, mode, onSelect, regions }: Props) {
  const source = regions ?? RUSSIA_REGIONS;
  const q = search.trim().toLowerCase();
  const visible = source.filter(r =>
    activeFD.has(r.district) &&
    (q === "" || r.name.toLowerCase().includes(q) || r.capital.toLowerCase().includes(q))
  );

  return (
    <div className="map-wrap" style={{ height }}>
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
        {visible.map(r => {
          const fill = mode === "esg" ? esgColor(r.esgScore) : FD_COLORS[r.district];
          const radius = mode === "esg" ? radByEsg(r.esgScore) : radByPop(r.population);
          return (
            <CircleMarker
              key={r.code}
              center={[r.lat, r.lon]}
              radius={radius}
              pathOptions={{ fillColor: fill, color: "#fff", weight: 1.25, fillOpacity: 0.9 }}
              eventHandlers={{ click: () => onSelect(r) }}
            >
              <Tooltip direction="top" offset={[0, -6]} className="esg-tooltip">
                <strong>{r.capital}</strong>
                <div style={{ fontSize: 11, opacity: 0.8 }}>{r.name}</div>
                <div style={{ fontSize: 10, opacity: 0.6 }}>
                  {r.district} - {(r.population / 1e6).toFixed(2)}M
                  {r.esgScore != null && ` - ESG ${r.esgScore}`}
                </div>
              </Tooltip>
            </CircleMarker>
          );
        })}
      </MapContainer>
    </div>
  );
}
