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
  BAND_COLOR, INPUT_LABELS, INPUT_ORDER, NEUTRAL_COLOR, describeInput, measuredPhysicalOf, reasonSentence,
  scoreOf, sourceLabelOf, type MapCountry,
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
  const physical = measuredPhysicalOf(c);
  const renew = physical.renewable_share_pct;
  const co2 = physical.co2_intensity_t_per_capita;
  const color = score === null || !c.band ? NEUTRAL_COLOR : BAND_COLOR[c.band] ?? NEUTRAL_COLOR;
  const missing = (c.missing_inputs ?? []).map((k) => INPUT_LABELS[k] ?? k);
  // Each missing input's reason, in the order the response names them. An input with no reason
  // shows its label. An older backend sends no reasons at all; then the plain list is shown.
  const reasonMap = c.input_reasons ?? null;
  const reasons = (c.missing_inputs ?? []).map((k) => {
    const r = reasonMap?.[k];
    return r ? reasonSentence(k, r) : INPUT_LABELS[k] ?? k;
  });

  return (
    <div className="tip">
      <div className="tip-name">
        <span className="tip-flag" style={{ background: color }} />
        {c.name}
      </div>

      {score === null ? (
        <>
          <div className="tip-score">
            <div className="tip-score-num">{c.client_expired ? "withheld" : "incomplete"}</div>
            <div className="tip-score-band">no score</div>
          </div>
          {c.client_expired ? (
            <div className="tip-missing">
              Score withheld: an input expired on {c.client_expired_on ?? "an unknown date"} (UTC); waiting for fresh data.
            </div>
          ) : reasonMap !== null ? (
            <ul className="tip-missing tip-reasons">
              {reasons.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : (
            <div className="tip-missing">
              Missing: {missing.length > 0 ? missing.join(", ") : "inputs not stated"}
            </div>
          )}
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

      {score !== null && c.inputs && (
        <ul className="tip-inputs">
          {INPUT_ORDER.filter((k) => c.inputs?.[k]).map((k) => {
            const line = describeInput(k, c.inputs![k]!);
            return (
              <li key={k}>
                <span>{line.label}</span> {line.text}
              </li>
            );
          })}
        </ul>
      )}
      {score !== null && (
        <div className="tip-source">source: {sourceLabelOf(c.esg_source, meanings)}</div>
      )}
      <div className="tip-cta">click to evaluate →</div>
    </div>
  );
}
