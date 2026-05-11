import { MapContainer, TileLayer, CircleMarker, Tooltip } from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import "leaflet/dist/leaflet.css";
import "./map.css";
import { RUSSIA_REGIONS, FD_COLORS } from "@/data/russia_regions";

const B: LatLngBoundsExpression = [[41, 19], [82, 180]];
const rad = (p: number) => 4 + Math.min(8, Math.log10(Math.max(p, 1000)) - 3);

export default function RussiaMap({ height = 560 }: { height?: number | string }) {
  return (
    <div className="map-wrap" style={{ height }}>
      <MapContainer bounds={B} minZoom={3} maxZoom={10} scrollWheelZoom attributionControl={false}
        style={{ height: "100%", width: "100%", background: "var(--bg-1)" }}>
        <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
        {RUSSIA_REGIONS.map(r => (
          <CircleMarker key={r.code} center={[r.lat, r.lon]} radius={rad(r.population)}
            pathOptions={{ fillColor: FD_COLORS[r.district], color: "#fff", weight: 1.25, fillOpacity: 0.9 }}>
            <Tooltip direction="top" offset={[0, -6]} className="esg-tooltip">
              <strong>{r.capital}</strong>
              <div style={{ fontSize: 11, opacity: 0.8 }}>{r.name}</div>
              <div style={{ fontSize: 10, opacity: 0.6 }}>{r.district} • {(r.population / 1e6).toFixed(2)}M</div>
            </Tooltip>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}
