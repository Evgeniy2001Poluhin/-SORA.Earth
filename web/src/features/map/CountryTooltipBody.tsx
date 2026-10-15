/**
 * The tooltip content for a country marker on the global map.
 *
 * Extracted from MapPage so it can be asserted on directly: react-leaflet's
 * Tooltip renders as a layer, not an element, so its content is not in the DOM
 * until a marker is opened in a real map.
 */
import { BAND_COLOR, measuredPhysicalOf, sourceKindOf, sourceLabelOf, type MapCountry } from "./countryData";

export default function CountryTooltipBody({ country: c }: { country: MapCountry }) {
  const physical = measuredPhysicalOf(c);
  const isStatic = sourceKindOf(c.esg_source) === "static";
  const renew = physical.renewable_share_pct;
  const co2 = physical.co2_intensity_t_per_capita;

  return (
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
        <div>
          <span>Renewables</span>
          <b className={renew == null ? "tip-nodata" : undefined}>{renew == null ? "no data" : `${renew}%`}</b>
        </div>
        <div>
          <span>CO2 / capita</span>
          <b className={co2 == null ? "tip-nodata" : undefined}>{co2 == null ? "no data" : `${co2} t`}</b>
        </div>
      </div>
      <div className={isStatic ? "tip-source tip-source-static" : "tip-source"}>
        source: {sourceLabelOf(c.esg_source)}
      </div>
      <div className="tip-cta">click to evaluate →</div>
    </div>
  );
}
