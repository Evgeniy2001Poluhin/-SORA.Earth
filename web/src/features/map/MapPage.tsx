import { useQuery } from "@tanstack/react-query";
import { MapContainer, TileLayer, CircleMarker, Tooltip, AttributionControl } from "react-leaflet";
import { useNavigate } from "react-router-dom";
import "leaflet/dist/leaflet.css";
import {
  TILE_URL, TILE_ATTRIBUTION, TILE_MAX_NATIVE_ZOOM, WORLD_BOUNDS,
} from "./tiles";
import { api } from "@/api/client";
import "./map.css";
import { useState } from "react";
import RussiaMapModal from "./RussiaMapModal";
import CountryTooltipBody from "./CountryTooltipBody";
import {
  BAND_COLOR, NEUTRAL_COLOR, aggregateScopeOf, byEsgDesc, meanScore, scoreOf, statusOf,
  type MapCountry,
} from "./countryData";

type MapData = {
  total_countries: number;
  bands: Record<string, string>;
  /** Token -> meaning for each `esg_source`. Absent from older backends. */
  esg_source_meanings?: Record<string, string>;
  countries: MapCountry[];
};

/** Radius for a scored country. A country with no score is drawn at the smallest size. */
const radiusByEsg = (esg: number | null) => (esg === null ? 3 : 3 + (esg / 100) * 4);

export default function MapPage() {
  const [ruOpen, setRuOpen] = useState(false);
  const nav = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["map-countries"],
    queryFn: () => api<MapData>("/map/countries"),
  });

  if (isLoading) return <div className="map-page"><p className="muted">Loading map...</p></div>;
  // A failed request is not an incomplete score: say the data is unavailable.
  if (error) return <div className="map-page"><p className="err">ESG map unavailable: the country scores could not be loaded.</p></div>;
  // #236: `{}` is truthy, so this passed an empty 200 to
  // `data.countries.filter` and blanked the map.
  if (!data || !Array.isArray(data.countries)) return null;

  // Leaders, top-N and averages are over scored countries only. See countryData.ts.
  const scope = aggregateScopeOf(data.countries);
  const ranked = byEsgDesc(scope.included);
  const leaders = scope.included.filter(c => c.band === "leader").length;
  const average = meanScore(scope.included);

  return (
    <div className="map-page">
      <header className="map-header">
        <div>
          <h1>Global ESG Map</h1>
          <p className="muted">
            N = {scope.included.length} of {scope.total} countries have a score. Click a marker to evaluate.
          </p>
          <p className="map-scope">Leaders, top and average: {scope.label}</p>
        </div>
        <div className="map-stats">
          <div className="stat"><b>{leaders}</b><em>leaders</em></div>
          {/* Separate from the payload guard above and more reachable: an
              empty list is a legitimate answer, and `countries[0].name`
              took the page down on it. */}
          {ranked.length > 0 && (
            <>
              <div className="stat"><b>{ranked[0].name}</b><em>top: {ranked[0].esg}</em></div>
              <div className="stat"><b>{average ?? "—"}</b><em>avg ESG</em></div>
            </>
          )}
        </div>
        <button onClick={() => setRuOpen(true)}
          style={{ marginTop: 8, padding: "6px 14px", borderRadius: 6,
            background: "#1a1d20", border: "1px solid #2a2e33", color: "var(--text)", cursor: "pointer" }}>
          🇷🇺 Россия — карта регионов
        </button>
      </header>

      <div className="map-legend">
        {Object.entries(BAND_COLOR).map(([band, color]) => (
          <div key={band} className="legend-item">
            <span className="dot pulse" style={{ background: color }} />
            <span>{band}</span>
            <em>{data.bands[band]}</em>
          </div>
        ))}
        <div className="legend-item">
          <span className="dot" style={{ background: NEUTRAL_COLOR }} />
          <span>incomplete</span>
          <em>no score: {scope.incomplete}</em>
        </div>
      </div>

      <div className="map-wrap">
        {/* `worldCopyJump` is gone with the repeating world it was there for:
            it moves the view to the nearest copy, and `noWrap` below leaves
            only one. The default `attributionControl` is left off the
            MapContainer and `<AttributionControl prefix={false}>` is
            rendered instead: OSM's own tile usage policy only requires
            crediting OpenStreetMap (the `TileLayer`'s `attribution` below) —
            "Leaflet"'s own credit is that library's default `prefix`, not
            anything OSM requires, and `prefix={false}` is Leaflet's
            documented way to drop it. */}
        <MapContainer
          center={[28, 10]}
          zoom={2}
          minZoom={2}
          maxZoom={6}
          scrollWheelZoom
          attributionControl={false}
          maxBounds={WORLD_BOUNDS}
          maxBoundsViscosity={1.0}
          style={{ height: 600, width: "100%", background: "var(--bg-1)" }}
        >
          <AttributionControl prefix={false} />
          {/* `noWrap` is what stops the world repeating. Without it the tile
              layer keeps painting copies past the bounds, while the markers
              exist once and appear on the first copy only. */}
          <TileLayer
            url={TILE_URL}
            attribution={TILE_ATTRIBUTION}
            maxNativeZoom={TILE_MAX_NATIVE_ZOOM}
            noWrap
          />

          {/* Main markers. An incomplete country is neutral and dashed: no band, no 0. */}
          {data.countries.map((c) => {
            const score = scoreOf(c);
            const incomplete = score === null;
            return (
              <CircleMarker
                key={c.code}
                center={[c.lat, c.lon]}
                radius={radiusByEsg(score)}
                pathOptions={{
                  color: "#ffffff",
                  fillColor: incomplete ? NEUTRAL_COLOR : (c.band ? BAND_COLOR[c.band] : undefined) || NEUTRAL_COLOR,
                  fillOpacity: incomplete ? 0.5 : 1,
                  weight: 1,
                  opacity: 0.9,
                  dashArray: incomplete ? "2 3" : undefined,
                }}
                eventHandlers={{
                  click: () => nav("/evaluate?country=" + encodeURIComponent(c.name)),
                }}
              >
                <Tooltip direction="top" offset={[0, -6]} opacity={1} className="esg-tooltip">
                  <CountryTooltipBody country={c} meanings={data.esg_source_meanings} />
                </Tooltip>
              </CircleMarker>
            );
          })}
        </MapContainer>
      </div>

      <section className="top-list">
        <h3>Top 10 by ESG</h3>
        <p className="map-scope">{scope.label}</p>
        <ol>
          {ranked.slice(0, 10).map((c, i) => (
            <li
              key={c.code}
              onClick={() => nav("/evaluate?country=" + encodeURIComponent(c.name))}
              style={{ borderLeft: `3px solid ${(c.band && BAND_COLOR[c.band]) || NEUTRAL_COLOR}` }}
            >
              <span className="rank">#{i + 1}</span>
              <b>{c.name}</b>
              <em>{c.esg}{statusOf(c) === "mixed" ? " (mixed)" : ""}</em>
            </li>
          ))}
        </ol>
      </section>
      <RussiaMapModal open={ruOpen} onClose={() => setRuOpen(false)} />
    </div>
  );
}
