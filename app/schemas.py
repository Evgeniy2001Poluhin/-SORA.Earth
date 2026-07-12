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
