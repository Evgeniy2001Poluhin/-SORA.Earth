/**
 * One basemap for every map in the app.
 *
 * It was `https://{s}.basemaps.cartocdn.com/dark_all/...`, written out twice —
 * once in MapPage.tsx and once in RussiaMap.tsx. CARTO now stamps
 * "API KEY REQUIRED / carto.com/basemaps/apikey" diagonally across every tile
 * it serves without a key, so both maps were delivered covered in watermarks.
 * No key has ever been in this repository; the policy changed under a URL that
 * did not.
 *
 * OpenStreetMap's standard tiles carry no watermark and need no account. They
 * are light, and `map.css` keeps the canvas dark on purpose — "markers need
 * contrast", and that holds in the light theme too — so the tile pane is
 * inverted in CSS rather than the design being changed to suit the tiles. The
 * filter is scoped to `.leaflet-tile-pane`; markers, tooltips and controls live
 * in other panes and are untouched.
 *
 * ATTRIBUTION is not decoration. OSM's tile usage policy requires visible
 * credit, and both maps previously passed `attributionControl={false}` — which
 * was already wrong for CARTO and would be a licence breach here. Every map
 * using these tiles must render it.
 */

/** No `{s}` subdomain: OSM has deprecated subdomain sharding over HTTP/2. */
export const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

export const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

/**
 * Zoom levels the source actually serves. Asking beyond `maxNativeZoom` returns
 * 404s that render as grey squares; Leaflet upscales the last real tile instead
 * when it is told where the source stops.
 */
export const TILE_MAX_NATIVE_ZOOM = 19;

/**
 * Longitude bounds for a map of the whole world.
 *
 * The previous value was `[[-65, -Infinity], [82, Infinity]]`. An infinite
 * longitude bound is not a bound: `maxBounds` was set and `maxBoundsViscosity`
 * was 1.0, and the world still scrolled sideways forever, so North America
 * appeared twice on screen with markers on only one of the copies — the report
 * that started this change.
 *
 * Latitude stops short of the poles because Web Mercator's are unusable, and
 * -65 keeps Antarctica out of a view that has no data for it.
 */
export const WORLD_BOUNDS: [[number, number], [number, number]] =
  [[-65, -180], [82, 180]];
