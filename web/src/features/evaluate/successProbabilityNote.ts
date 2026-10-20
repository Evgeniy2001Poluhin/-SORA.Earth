// Wording for every place the ML success probability is shown.
//
// The model learned "success" from an administrative proxy (a project closed
// after its scheduled end), not from independently verified outcomes. Until a
// model trained on real outcomes replaces it, each display of the number says
// so. Import these constants rather than copying the text.

export const SUCCESS_PROBABILITY_LABEL = "ML success probability · unvalidated";

export const SUCCESS_PROBABILITY_NOTE =
  "Model estimate, not a validated forecast. The model learned \"success\" from an administrative proxy (a project closed after its scheduled end), not from independently verified outcomes. Treat it as indicative.";

// Under the training-time metrics. Those scores are measured against the same
// proxy label, so the note states the split and the limit, not the prediction.
export const TRAINING_METRICS_NOTE =
  "Training-time evaluation on a held-out split of the training data. The label is an administrative proxy (a project closed after its scheduled end), not an independently verified outcome, so these scores measure agreement with that proxy.";
