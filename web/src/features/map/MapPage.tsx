import { useQuery } from "@tanstack/react-query";
import { MapContainer, TileLayer, CircleMarker, Tooltip } from "react-leaflet";
import { useNavigate } from "react-router-dom";
import type { LatLngBoundsExpression } from "leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "@/api/client";
import "./map.css";

type Country = {
  code: string; name: string; lat: number; lon: number; esg: number;
  band: string; co2_intensity_t_per_capita: number; renewable_share_pct: number;
};
type MapData = { total_countries: number; bands: Record<string, string>; countries: Country[] };

const BAND_COLOR: Record<string, string> = {
  leader:     "#16a34a",
  advanced:   "#65a30d",
  developing: "#ca8a04",
  emerging:   "#ea580c",
  lagging:    "#dc2626",
};

const WORLD_BOUNDS: LatLngBoundsExpression = [[-65, -Infinity], [82, Infinity]];

const radiusByEsg = (esg: number) => 3 + (esg / 100) * 4;
const haloRadius  = (esg: number) => 4 + (esg / 100) * 5;

export default function MapPage() {
  const nav = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["map-countries"],
    queryFn: () => api<MapData>("/map/countries"),
  });

  if (isLoading) return <div className="map-page"><p className="muted">Loading map...</p></div>;
  if (error)     return <div className="map-page"><p className="err">{(error as Error).message}</p></div>;
  if (!data)     return null;

  return (
    <div className="map-page">
      <header className="map-header">
        <div>
          <h1>Global ESG Map</h1>
          <p className="muted">
            {data.total_countries} countries, ESG color-coded, click marker to evaluate
          </p>
        </div>
        <div className="map-stats">
          <div className="stat"><b>{data.countries.filter(c => c.band === "leader").length}</b><em>leaders</em></div>
          <div className="stat"><b>{data.countries[0].name}</b><em>top: {data.countries[0].esg}</em></div>
          <div className="stat"><b>{Math.round(data.countries.reduce((s,c)=>s+c.esg,0)/data.countries.length)}</b><em>avg ESG</em></div>
        </div>
      </header>

      <div className="map-legend">
        {Object.entries(BAND_COLOR).map(([band, color]) => (
          <div key={band} className="legend-item">
            <span className="dot pulse" style={{ background: color }} />
            <span>{band}</span>
            <em>{data.bands[band]}</em>
          </div>
        ))}
      </div>

      <div className="map-wrap">
        <MapContainer
          center={[28, 10]}
          zoom={2}
          minZoom={2}
          maxZoom={6}
          scrollWheelZoom
          worldCopyJump
          attributionControl={false}
          maxBounds={WORLD_BOUNDS}
          maxBoundsViscosity={1.0}
          style={{ height: 600, width: "100%", background: "#05080a" }}
        >
          <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />

          {/* Main markers */}
          {data.countries.map((c) => (
            <CircleMarker
              key={c.code}
              center={[c.lat, c.lon]}
              radius={radiusByEsg(c.esg)}
              pathOptions={{
                color: "#ffffff",
                fillColor: BAND_COLOR[c.band] || "#888",
                fillOpacity: 1,
                weight: 1,
                opacity: 0.9,
              }}
              eventHandlers={{
                click: () => nav("/evaluate?country=" + encodeURIComponent(c.name)),
              }}
            >
              <Tooltip direction="top" offset={[0, -6]} opacity={1} className="esg-tooltip">
                <div className="tip">
                  <div className="tip-name">
                    <span className="tip-flag" style={{ background: BAND_COLOR[c.band] }} />
                    {c.name}
                  </div>
                  <div className="tip-score">
                    <div className="tip-score-num">{c.esg}</div>
                    <div className="tip-score-band">{c.band}</div>
                  </div>
                  <div className="tip-bar">
                    <div className="tip-bar-fill" style={{ width: c.esg + "%", background: BAND_COLOR[c.band] }} />
                  </div>
                  <div className="tip-rows">
                    <div><span>Renewables</span><b>{c.renewable_share_pct}%</b></div>
                    <div><span>CO2 / capita</span><b>{c.co2_intensity_t_per_capita} t</b></div>
                  </div>
                  <div className="tip-cta">click to evaluate →</div>
                </div>
              </Tooltip>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>

      <section className="top-list">
        <h3>Top 10 by ESG</h3>
        <ol>
          {data.countries.slice(0, 10).map((c, i) => (
            <li
              key={c.code}
              onClick={() => nav("/evaluate?country=" + encodeURIComponent(c.name))}
              style={{ borderLeft: `3px solid ${BAND_COLOR[c.band] || "#888"}` }}
            >
              <span className="rank">#{i + 1}</span>
              <b>{c.name}</b>
              <em>{c.esg}</em>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
