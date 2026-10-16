/**
 * The MLOps page must not draw an absent value as a measurement.
 *
 * Each of these used to read the backend's silence as a number: a missing
 * accuracy was handed to the chart as a bar at 0, a missing observation count
 * printed 0, a missing threshold printed 0.00, a missing artifact list printed
 * "0 artifacts", and `no_baseline` was coloured like a stable model. Absent and
 * a measured zero are different answers, so the zero cases below check that a
 * real 0 still shows as 0.
 */
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

vi.mock("@/api/mock", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/mock")>();
  return { ...actual, isMock: false };
});

/** The bar data the chart was handed. Captured rather than drawn: jsdom has no
 *  layout, so a drawn chart gives a test nothing to read. */
const chart = vi.hoisted(() => ({
  data: [] as Array<{ name: string; value: number | null }>,
}));

vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) => children,
    BarChart: ({ data }: { data: Array<{ name: string; value: number | null }> }) => {
      chart.data = data;
      return null;
    },
  };
});

// The three panels below issue their own requests. They are not what this
// file is about, and their own tests live beside them.
vi.mock("./RetrainLogPanel", () => ({ RetrainLogPanel: () => null }));
vi.mock("./FeatureImportancePanel", () => ({ FeatureImportancePanel: () => null }));
vi.mock("./SchedulerPanel", () => ({ SchedulerPanel: () => null }));

import { MlopsHealthPage } from "./MlopsHealthPage";
import { isMock } from "@/api/mock";
import { renderWithQuery } from "@/test/utils";
import { SUCCESS_PROBABILITY_NOTE } from "../evaluate/successProbabilityNote";

const META = {
  retrained_at: "20260712_200332",
  algorithm: "RandomForestClassifier",
  n_estimators: 200,
  max_depth: 10,
  features: ["budget", "co2_reduction"],
  total_samples: 17070,
};

const HEALTH = {
  model_status: "healthy",
  drift_status: "stable",
  observations_tracked: 147,
  monitoring: { prometheus: "/metrics", mlflow: "/mlflow/stats", drift: "/mlops/drift" },
};

const STATUS = {
  current_threshold: 0.37,
  meta: META,
  retrain_history: [],
  prediction_log_size: 0,
};

const METRICS = {
  metrics: {
    accuracy: 0.8641,
    f1_score: 0.9016,
    best_f1: 0.905,
    roc_auc: 0.905,
    best_threshold: 0.45,
    train_samples: 13656,
    test_samples: 3414,
  },
  meta: META,
  models_available: ["model.pkl", "xgb_model.pkl"],
};

interface Parts {
  health: unknown;
  status: unknown;
  metrics: unknown;
}

/** Answers the three MLOps queries with the parts given. The page issues
 *  nothing else, because the child panels are mocked above. */
function stubMlops(parts: Parts) {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.includes("/mlops/health")
      ? parts.health
      : url.includes("/model/status")
        ? parts.status
        : url.includes("/model/metrics")
          ? parts.metrics
          : [];
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

/** A copy of `obj` with `key` removed, so the field is absent rather than set
 *  to undefined in a way a reader could mistake for a value. */
function without(obj: object, key: string): Record<string, unknown> {
  return Object.fromEntries(Object.entries(obj).filter(([k]) => k !== key));
}

/** The full answer set, with `overrides` applied to one or more parts. */
function full(overrides: Partial<Parts> = {}): Parts {
  return { health: HEALTH, status: STATUS, metrics: METRICS, ...overrides };
}

const renderPage = () => renderWithQuery(<MlopsHealthPage />);

/** Once the page has loaded, so the reads below see answered queries. */
const loaded = () => screen.findByText("MLOps Control Room");

/** The KPI tile whose label matches, scoped to `.kpi-lbl` so that a table
 *  header with the same word does not match too. */
function kpiValue(label: string): HTMLElement {
  const labelNode = screen
    .getAllByText(label)
    .find((el) => el.className.includes("kpi-lbl"));
  const node = labelNode?.parentElement?.querySelector<HTMLElement>(".kpi-val");
  if (!node) throw new Error(`no KPI labelled ${label}`);
  return node;
}

const kpi = (label: string) => (kpiValue(label).textContent ?? "").trim();

function barValue(name: string): number | null | undefined {
  return chart.data.find((b) => b.name === name)?.value;
}

describe("the guard on these tests", () => {
  it("really is in production mode", () => {
    expect(isMock).toBe(false);
  });
});

describe("performance metrics when the backend omits some of them", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    chart.data = [];
  });

  it("does not hand a missing accuracy to the chart as a zero", async () => {
    stubMlops(
      full({ metrics: { ...METRICS, metrics: { ...METRICS.metrics, accuracy: undefined } } }),
    );
    renderPage();
    await loaded();

    expect(barValue("accuracy")).toBeNull();
  });

  it("does not hand any of the four missing metrics to the chart as a zero", async () => {
    stubMlops(full({ metrics: { ...METRICS, metrics: { test_samples: 3414, train_samples: 13656 } } }));
    renderPage();
    await loaded();

    for (const name of ["accuracy", "f1", "roc_auc", "best_f1"]) {
      expect(barValue(name)).toBeNull();
    }
  });

  it("says a missing metric is unavailable rather than printing a zero", async () => {
    stubMlops(
      full({ metrics: { ...METRICS, metrics: { ...METRICS.metrics, accuracy: undefined } } }),
    );
    renderPage();
    await loaded();

    expect(screen.getByText("accuracy unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/accuracy 0/)).toBeNull();
  });

  it("still draws a measured zero as zero", async () => {
    stubMlops(full({ metrics: { ...METRICS, metrics: { ...METRICS.metrics, accuracy: 0 } } }));
    renderPage();
    await loaded();

    expect(barValue("accuracy")).toBe(0);
    expect(screen.getByText("accuracy 0.000")).toBeInTheDocument();
  });

  it("still shows the real values it was sent", async () => {
    stubMlops(full());
    renderPage();
    await loaded();

    expect(barValue("f1")).toBe(0.9016);
    expect(screen.getByText("f1 0.902")).toBeInTheDocument();
    expect(screen.getByText("accuracy 0.864")).toBeInTheDocument();
  });

  it("says what the metrics were measured on, and the proxy-label limit", async () => {
    stubMlops(full());
    renderPage();
    await loaded();

    expect(
      screen.getByText(
        "Training-time evaluation on a held-out test set of 3414 samples (training set: 13656).",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(SUCCESS_PROBABILITY_NOTE)).toBeInTheDocument();
  });

  it("says the split size was not reported when it is missing", async () => {
    stubMlops(
      full({ metrics: { ...METRICS, metrics: { ...METRICS.metrics, test_samples: undefined } } }),
    );
    renderPage();
    await loaded();

    expect(screen.getByText("Test-split size not reported.")).toBeInTheDocument();
  });
});

describe("observations and threshold when the backend omits them", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    chart.data = [];
  });

  it("shows observations as unknown, not zero", async () => {
    stubMlops(full({ health: without(HEALTH, "observations_tracked") }));
    renderPage();
    await loaded();

    expect(kpi("Observations")).toBe("—");
    expect(kpi("Observations")).not.toBe("0");
  });

  it("keeps a measured zero observation count as zero", async () => {
    stubMlops(full({ health: { ...HEALTH, observations_tracked: 0 } }));
    renderPage();
    await loaded();

    expect(kpi("Observations")).toBe("0");
  });

  it("shows the threshold as unknown, not 0.00", async () => {
    stubMlops(full({ status: without(STATUS, "current_threshold") }));
    renderPage();
    await loaded();

    expect(kpi("Threshold")).toBe("—");
    expect(kpi("Threshold")).not.toBe("0.00");
  });

  it("keeps a measured zero threshold as 0.00", async () => {
    stubMlops(full({ status: { ...STATUS, current_threshold: 0 } }));
    renderPage();
    await loaded();

    expect(kpi("Threshold")).toBe("0.00");
  });
});

describe("drift and model colour", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    chart.data = [];
  });

  it("does not colour no_baseline like a healthy, stable model", async () => {
    stubMlops(full({ health: { ...HEALTH, drift_status: "no_baseline" } }));
    renderPage();
    await loaded();

    expect(kpi("Drift")).toBe("NO BASELINE");
    expect(kpiValue("Drift").style.color).not.toBe("var(--planet)");
  });

  it("keeps a stable drift reading green", async () => {
    stubMlops(full());
    renderPage();
    await loaded();

    expect(kpi("Drift")).toBe("STABLE");
    expect(kpiValue("Drift").style.color).toBe("var(--planet)");
  });

  it("does not colour an absent model status as a failure", async () => {
    stubMlops(full({ health: without(HEALTH, "model_status") }));
    renderPage();
    await loaded();

    expect(kpi("Model")).toBe("UNKNOWN");
    expect(kpiValue("Model").style.color).not.toBe("var(--danger, #EF4444)");
  });
});

describe("model artifacts", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    chart.data = [];
  });

  it("shows the artifact count as unknown, not zero, when the list is missing", async () => {
    stubMlops(full({ metrics: without(METRICS, "models_available") }));
    renderPage();
    await loaded();

    expect(screen.getByText("Model artifacts · —")).toBeInTheDocument();
    expect(screen.queryByText("Model artifacts · 0")).toBeNull();
  });

  it("keeps an empty list as a count of zero", async () => {
    stubMlops(full({ metrics: { ...METRICS, models_available: [] } }));
    renderPage();
    await loaded();

    expect(screen.getByText("Model artifacts · 0")).toBeInTheDocument();
  });

  it("lists the artifacts it was sent", async () => {
    stubMlops(full());
    renderPage();
    await loaded();

    expect(screen.getByText("Model artifacts · 2")).toBeInTheDocument();
    expect(screen.getByText("model.pkl")).toBeInTheDocument();
  });
});

describe("refresh wording and sample labels", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    chart.data = [];
  });

  it("does not say every query auto-refreshes every 10 seconds", async () => {
    stubMlops(full());
    renderPage();
    await loaded();

    expect(screen.queryByText(/Auto-refresh every 10s/)).toBeNull();
    expect(
      screen.getByText(/Health and drift refresh every 10s; model status and metrics load when the page opens/),
    ).toBeInTheDocument();
  });

  it("labels the sample count as the total it is, not as the training count", async () => {
    stubMlops(full());
    renderPage();
    await loaded();

    expect(screen.queryByText("Train samples")).toBeNull();
    expect(kpi("Total samples")).toBe("17070");
  });
});
