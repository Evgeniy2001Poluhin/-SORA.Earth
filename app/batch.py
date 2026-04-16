from pydantic import BaseModel
from typing import List, Optional
import uuid


class BatchRequest(BaseModel):
    projects: List[dict]
    callback_url: Optional[str] = None


class BatchResultSchema(BaseModel):
    batch_id: str
    total: int
    successful: int
    failed: int
    results: List[dict]
    processing_time_ms: float


def generate_batch_id():
    return f"batch_{uuid.uuid4().hex[:12]}"
