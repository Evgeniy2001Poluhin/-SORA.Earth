/**
 * The tooltip content for a country marker on the global map.
 *
 * Extracted from MapPage so it can be asserted on directly: react-leaflet's
 * Tooltip renders as a layer, not an element, so its content is not in the DOM
 * until a marker is opened in a real map.
 *
 * A country with no score says "incomplete" and names what is missing. It never
 * shows a number in place of the score.
 */
import {
  BAND_COLOR, INPUT_LABELS, NEUTRAL_COLOR, measuredPhysicalOf, scoreOf, sourceLabelOf, statusOf,
  type MapCountry,
} from "./countryData";

export default function CountryTooltipBody({
  country: c,
  meanings,
}: {
  country: MapCountry;
  /** `esg_source_meanings` from the response, when it sent one. */
  meanings?: Readonly<Record<string, string>> | null;
}) {
  const score = scoreOf(c);
  const status = statusOf(c);
  const physical = measuredPhysicalOf(c);
  const renew = physical.renewable_share_pct;
  const co2 = physical.co2_intensity_t_per_capita;
  const color = score === null || !c.band ? NEUTRAL_COLOR : BAND_COLOR[c.band] ?? NEUTRAL_COLOR;
  const missing = (c.missing_inputs ?? []).map((k) => INPUT_LABELS[k] ?? k);

  return (
    <div className="tip">
      <div className="tip-name">
        <span className="tip-flag" style={{ background: color }} />
        {c.name}
      </div>

      {score === null ? (
        <>
          <div className="tip-score">
            <div className="tip-score-num">incomplete</div>
            <div className="tip-score-band">no score</div>
          </div>
          <div className="tip-missing">
            Missing: {missing.length > 0 ? missing.join(", ") : "inputs not stated"}
          </div>
        </>
      ) : (
        <>
          <div className="tip-score">
            <div className="tip-score-num">{score}</div>
            <div className="tip-score-band">{c.band}</div>
          </div>
          <div className="tip-bar">
            <div className="tip-bar-fill" style={{ width: score + "%", background: color }} />
          </div>
          {status === "mixed" && (
            <div className="tip-status">
              mixed: some inputs are static reference values with a stated source
            </div>
          )}
        </>
      )}

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

      {score !== null && (
        <div className="tip-source">source: {sourceLabelOf(c.esg_source, meanings)}</div>
      )}
      <div className="tip-cta">click to evaluate →</div>
    </div>
  );
}
