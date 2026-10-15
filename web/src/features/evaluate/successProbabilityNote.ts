// Wording for every place the ML success probability is shown.
//
// The model learned "success" from an administrative proxy (a project closed
// after its scheduled end), not from independently verified outcomes. Until a
// model trained on real outcomes replaces it, each display of the number says
// so. Import these constants rather than copying the text.

export const SUCCESS_PROBABILITY_LABEL = "ML success probability · unvalidated";

export const SUCCESS_PROBABILITY_NOTE =
  "Model estimate, not a validated forecast. The model learned \"success\" from an administrative proxy (a project closed after its scheduled end), not from independently verified outcomes. Treat it as indicative.";
