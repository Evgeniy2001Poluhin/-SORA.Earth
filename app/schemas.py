from pydantic import BaseModel, Field
from typing import Optional

class ProjectInput(BaseModel):
    name: str = "Project"
    budget: float = Field(default=50000, ge=0, description="Budget in USD")
    co2_reduction: float = Field(default=50, ge=0, description="CO2 reduction tons/year")
    social_impact: float = Field(default=5, ge=1, le=10, description="Social impact score 1-10")
    duration_months: int = Field(default=12, ge=1, le=120, description="Duration in months")
    category: Optional[str] = "Solar Energy"
    region: Optional[str] = "Europe"
    lat: Optional[float] = 50.0
    lon: Optional[float] = 10.0

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
