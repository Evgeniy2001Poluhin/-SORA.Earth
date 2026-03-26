from pydantic import BaseModel, validator

class ProjectInput(BaseModel):
    budget: float
    co2_reduction: float
    social_impact: float
    duration_months: float

    @validator("budget")
    def budget_positive(cls, v):
        if v < 0:
            raise ValueError("budget must be >= 0")
        if v > 1e12:
            raise ValueError("budget too large")
        return v

    @validator("co2_reduction")
    def co2_positive(cls, v):
        if v < 0:
            raise ValueError("co2_reduction must be >= 0")
        return v

    @validator("social_impact")
    def social_range(cls, v):
        if not 0 <= v <= 10:
            raise ValueError("social_impact must be 0-10")
        return v

    @validator("duration_months")
    def duration_positive(cls, v):
        if v <= 0:
            raise ValueError("duration_months must be > 0")
        if v > 600:
            raise ValueError("duration_months too large")
        return v
