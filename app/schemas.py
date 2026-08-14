from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, List, Dict, Any, Literal

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
