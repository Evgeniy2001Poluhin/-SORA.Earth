from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Annotated, Optional, List, Dict, Any, Literal, Union

class ProjectInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = "Project"
    budget: float = Field(
        default=50000,
        ge=1000,
        le=100_000_000_000,
        alias="budget_usd",
        description="Budget in USD (range: $1K to $100B based on training data)"
    )
    co2_reduction: float = Field(
        default=50,
        ge=0,
        le=10000,
        alias="co2_reduction_tons_per_year",
        description="CO2 reduction tons/year (max 10K tons based on training data)"
    )
    social_impact: float = Field(
        default=5,
        ge=0,
        le=100,
        alias="social_impact_score",
        description="Social impact score 0-100 (normalized to match training data range)"
    )
    duration_months: int = Field(
        default=12,
        ge=1,
        le=120,
        alias="project_duration_months",
        description="Duration in months (1-120 range from training data)"
    )
    category: Optional[str] = "Solar Energy"
    region: Optional[str] = Field(default="Europe", alias="country")
    lat: Optional[float] = 50.0
    lon: Optional[float] = 10.0

    @field_validator("budget")
    @classmethod
    def validate_budget_range(cls, v: float) -> float:
        """Validate budget is within training data range to prevent extrapolation."""
        if v < 1000:
            raise ValueError(f"budget ${v:,.0f} below training range (min $1,000)")
        if v > 100_000_000_000:
            raise ValueError(f"budget ${v:,.0f} exceeds training range (max $100B)")
        return v

    @field_validator("co2_reduction")
    @classmethod
    def validate_co2_range(cls, v: float) -> float:
        """Validate CO2 is within training data range."""
        if v < 0:
            raise ValueError("co2_reduction cannot be negative")
        if v > 10000:
            raise ValueError(f"co2_reduction {v:,.1f} tons exceeds training range (max 10K tons/year)")
        return v

class GHGInput(BaseModel):
    electricity_kwh: float = Field(default=10000, ge=0)
    natural_gas_m3: float = Field(default=500, ge=0)
    diesel_liters: float = Field(default=200, ge=0)
    petrol_liters: float = Field(default=300, ge=0)
    flights_km: float = Field(default=5000, ge=0)
    waste_kg: float = Field(default=1000, ge=0)

class ESGResult(BaseModel):
    total_score: float
    environment_score: float
    social_score: float
    economic_score: float
    success_probability: float
    recommendations: list
    risk_level: str
    esg_weights: dict
    region: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


# ─── Forecasting Schemas ───────────────────────────────────────────────────

class ForecastPoint(BaseModel):
    ds: str = Field(..., description="Date in YYYY-MM-DD format")
    yhat: float = Field(..., description="Point prediction")
    yhat_lower: float = Field(..., description="Lower 90% confidence bound")
    yhat_upper: float = Field(..., description="Upper 90% confidence bound")


class HistoryPoint(BaseModel):
    ds: str
    y: float


class ForecastResponse(BaseModel):
    history: List[HistoryPoint]
    forecast: List[ForecastPoint]
    model: str
    metric: str
    confidence: Optional[Literal["high", "medium", "low"]] = None
    metadata: Optional[Dict[str, Any]] = None


class ForecastCacheStats(BaseModel):
    cache_size: int
    invalidated: Optional[int] = None

# --- ingestion attention (#74) ----------------------------------------------


class IngestionAttentionRow(BaseModel):
    """One source, and the verdict on its latest finished run.

    Every input the action was derived from travels with it. A row holding
    `escalate` and a vintage of 590 days cannot be re-read a month later
    without the tolerance that was in force -- a threshold can move, and then
    nobody can tell whether the data was old or the number changed.
    """

    source: str
    required_action: Optional[str] = None
    reason_code: Optional[str] = None
    status: Optional[str] = None
    source_vintage_seconds: Optional[float] = None
    max_vintage_seconds: Optional[float] = None
    freshness_status: Optional[str] = None
    records_received: Optional[int] = None
    records_accepted: Optional[int] = None
    records_rejected: Optional[int] = None
    failure_reason: Optional[str] = None
    finished_at: Optional[str] = None


class IngestionAttention(BaseModel):
    """Sources ordered by what they need, not by when they last ran.

    `needs_attention` counts everything that is not `none`, including rows whose
    action is NULL: a record that cannot be read is not one to skip past.
    """

    count: int
    needs_attention: int
    sources: List[IngestionAttentionRow]


class CoverageGap(BaseModel):
    """One day a point did not supply enough observations to be usable.

    The M3 declaration binds the target to a daily mean computed only when at
    least 80% of the expected hourly observations are present. A day below that
    is absent, and an absent day is one fewer window the §7 gate can use --
    which moves the earliest evidential date.

    Nothing watched this. `/ingestion/attention` reports the verdict of each
    source's latest *run*: it sees "the ingester stopped", and only weakly,
    since openmeteo declares no `max_vintage_hours`. A day where 18 of 24
    hourly observations arrived leaves every run successful and every freshness
    check content.
    """

    day: str
    region_id: str
    indicator: str
    observations: int
    required: int


class ObservationCoverage(BaseModel):
    """Days that fall short, and what was asked to find them.

    `complete_days` is reported beside `gaps` on purpose. A response holding an
    empty list means either "nothing fell short" or "nothing was looked at",
    and those are different facts about a deployment -- the denominator tells
    them apart.
    """

    source: str
    indicator: str
    required_per_day: int
    days_examined: int
    points_examined: int
    complete_days: int
    gap_count: int
    gaps: List[CoverageGap]


class ObservationRow(BaseModel):
    """One environmental observation, with its provenance in typed fields.

    #84. The table was written by three ingesters and read by no API at all, so
    the one thing the air-quality work was careful to record -- that a CAMS
    reanalysis is a model's estimate and not an instrument's reading -- had
    nobody to tell.

    `measurement_kind` and `model` are not columns and are not parsed out of
    `metadata_json` per row. They come from `app/ingesters/source_register.py`,
    which states them once per source: a per-row copy can disagree with the
    declaration, and then two answers to one question exist with nothing to
    choose between them.

    `metadata_json` itself is never handed to the caller. It is free-form text
    that happens to hold JSON today, and publishing it would make every key any
    ingester ever wrote part of this contract.
    """

    id: int
    region_id: str
    country_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    indicator: str
    value: Optional[float] = None
    unit: Optional[str] = None

    source: str
    #: measured | modelled | administrative_snapshot | static_baseline
    measurement_kind: str
    #: The named model behind a `modelled` value; None for every other kind.
    model: Optional[str] = None

    #: When the thing happened. NULL where the source has no observation time,
    #: which is a fact about the source and not a gap to fill (#121).
    event_time: Optional[str] = None
    #: When this system read it. Never a substitute for the above: writing the
    #: fetch time into `event_time` is what stamped 12,240 rows as observed on
    #: the day they were ingested.
    ingested_at: Optional[str] = None
    #: observed | period | not_applicable | legacy_ingestion_time
    temporal_kind: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None

    is_valid: bool


class ObservationPage(BaseModel):
    """A page of observations, and what was asked for to get it.

    The filters travel back with the rows. A page of 50 modelled readings looks
    identical to a page of 50 measured ones, and a caller that lost track of
    which it requested cannot recover it from the rows alone -- every row would
    agree with either belief.
    """

    count: int
    limit: int
    offset: int
    #: True when more rows match the filter than this page carries.
    has_more: bool
    #: Echoed exactly as applied, including the defaults the caller did not set.
    filters: dict
    observations: List[ObservationRow]


# --- GET /api/v1/model/drift ------------------------------------------------
#
# The first migrated contract (#239). The endpoint used to answer 200 with four
# incompatible bodies: the verdict was called `drift` on two branches,
# `drift_detected` on a third, and was absent on the fourth. A consumer written
# against the successful branch -- the only one visible in development -- read
# `undefined` everywhere else, and `undefined` reads as "no drift".
#
# Three rules hold the shape together:
#
#   * every 200 carries the same field set, so no consumer ever reads a missing
#     key;
#   * `drift_detected` is a boolean only when a verdict was actually computed,
#     and `null` otherwise -- never `false`, which is a claim nobody made;
#   * a broken deployment is not a data state. Missing SciPy is a 503, because
#     scipy==1.13.1 is a declared dependency and its absence is a fault, not an
#     answer about drift.


class KsFeatureStat(BaseModel):
    """One feature's two-sample KS result."""

    ks_stat: float
    p_value: float
    drift: bool


class _ModelDriftBase(BaseModel):
    """Fields every answer carries, whatever its status."""

    window: int = Field(..., description="Size of the recent window requested.")
    observations: int = Field(
        ..., description="Rows actually available in that window; 0 when there is no log."
    )
    features: Dict[str, KsFeatureStat] = Field(
        default_factory=dict,
        description=(
            "Per-feature KS results. Empty means 'no feature produced a result' "
            "ONLY when status is 'ok'; on every other status nothing was measured."
        ),
    )
    reason_code: Optional[str] = Field(
        None, description="Machine-readable detail for a non-'ok' status."
    )


class ModelDriftMeasured(_ModelDriftBase):
    """A verdict was computed."""

    status: Literal["ok"]
    drift_detected: bool


class ModelDriftNotMeasured(_ModelDriftBase):
    """A legitimate domain state: there was not enough to compute a verdict.

    Still 200, because "no data yet" is an answer about the world rather than a
    failure. `drift_detected` is null and not false: false would assert that
    drift was looked for and not found.
    """

    status: Literal["no_log", "insufficient_data"]
    drift_detected: None = None


class ModelDriftUnavailable(_ModelDriftBase):
    """The service could not run the test at all. Served with 503, never 200."""

    status: Literal["unavailable"]
    drift_detected: None = None


ModelDriftResponse = Annotated[
    Union[ModelDriftMeasured, ModelDriftNotMeasured],
    Field(discriminator="status"),
]


# --- GET /api/v1/model/drift/mlflow-history ---------------------------------
#
# Second migration under docs/API_CONTRACT_ROADMAP.md. Three bodies became two,
# and the one that mattered stopped being a 200.
#
# MLflow being unreachable used to answer 200 with
# `{"events": [], "count": 0, "error": "<exception text>"}`. On screen that is
# "Drift timeline (MLflow): 0 events" -- indistinguishable from a working MLflow
# with nothing recorded. One is a fault, the other is a fact, and the operator
# could not tell them apart. The exception text was also echoed to the caller.
#
# Unlike the KS report next door, an empty list here IS a measurement: MLflow
# answered and holds no drift events. So there are two statuses, not three.


class MlflowHistoryOk(BaseModel):
    """MLflow answered. `events` may be empty, and that is a real answer."""

    status: Literal["ok"]
    #: Rows as MLflow returned them. Deliberately not pinned field by field: the
    #: keys are MLflow column names selected at query time ("metrics.drift_score"
    #: and friends, which are not Python identifiers), and the set varies with
    #: what the tracking server actually stored. A stricter model would either
    #: drop columns or promise ones that are often absent.
    events: List[Dict[str, Any]] = Field(default_factory=list)
    count: int
    reason_code: Optional[str] = None


class MlflowHistoryUnavailable(BaseModel):
    """MLflow could not be queried. Served with 503, never 200."""

    status: Literal["unavailable"]
    events: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    #: A fixed machine-readable string. The exception text is logged, not
    #: returned: it reached the caller before, and an error message from a
    #: tracking client is not something a browser should be shown.
    reason_code: str


MlflowHistoryResponse = MlflowHistoryOk


# --- POST /api/v1/evaluate/monte-carlo --------------------------------------
#
# Fourth migration under docs/API_CONTRACT_ROADMAP.md.
#
# "Every one of N simulations raised" used to answer 200 with
# `{"error": "no successful runs"}`. A caller reading `mean` got `undefined`,
# and `undefined` reads as zero on the way to a chart. It is not a state of the
# world -- it is the service failing to compute anything, with the reasons
# discarded by `except Exception: continue`.
#
# The second fix here is `n`. It counted *successful* runs while reading as the
# number requested: ask for 1000, have 950 raise, and the answer says `n: 50`
# with no way to tell that from a request for 50. `requested` is now stated
# beside it, so a distribution computed from a fraction of the sample says so.


class MonteCarloHistogram(BaseModel):
    edges: List[float]
    counts: List[int]


class MonteCarloOk(BaseModel):
    """A distribution was computed. `n` may still be below `requested`."""

    status: Literal["ok"]
    #: Simulations asked for, after clamping to the endpoint's own bounds.
    requested: int
    #: Simulations that produced a score. Kept under its original name: the
    #: only consumer reads `n`, and it has always meant "successful runs".
    n: int
    #: Simulations that raised. `requested == n + failed` always holds, so a
    #: caller can see a shortfall instead of inferring one.
    failed: int
    mean: float
    stdev: float
    min: float
    max: float
    p10: float
    p50: float
    p90: float
    histogram: MonteCarloHistogram
    reason_code: Optional[str] = None


class MonteCarloUnavailable(BaseModel):
    """Nothing could be computed. Served with 503, never 200.

    503 rather than 500 or 422: a computation service that produced no result
    at all is unavailable for the purpose asked of it. The request itself
    passed validation, and the endpoint clamps every field into range before
    simulating, so it cannot claim the input is at fault.
    """

    status: Literal["unavailable"]
    requested: int
    n: int = 0
    failed: int
    reason_code: str
