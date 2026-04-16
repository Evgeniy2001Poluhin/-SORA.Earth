#!/bin/bash
set -e

echo "=== Step 1: Add BatchResultDB to app/database.py ==="
python3 << 'PYEOF1'
content = open('app/database.py').read()
if 'BatchResultDB' in content:
    print('BatchResultDB already exists in database.py, skipping')
else:
    new_class = '''

class BatchResultDB(Base):
    __tablename__ = "batch_results"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    job_type = Column(String(50), nullable=False, default="batch_evaluate")
    total = Column(Integer, nullable=False)
    successful = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Float, nullable=True)
    status = Column(String(50), nullable=False, default="completed")
    error_message = Column(Text, nullable=True)
    results_json = Column(Text, nullable=True)
    trigger_source = Column(String(50), nullable=False, default="manual")


'''
    content = content.replace('def init_db():', new_class + 'def init_db():')
    open('app/database.py', 'w').write(content)
    print('BatchResultDB added to database.py')
PYEOF1

echo "=== Step 2: Rewrite app/batch.py ==="
cat > app/batch.py << 'BATCHEOF'
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
BATCHEOF
echo "batch.py rewritten"

echo "=== Step 3: Patch app/api/infra.py ==="
python3 << 'PYEOF2'
import re

content = open('app/api/infra.py').read()

# Fix import line
content = content.replace(
    'from app.batch import BatchRequest, batch_history, generate_batch_id',
    'from app.batch import BatchRequest, generate_batch_id\nfrom app.database import get_db, BatchResultDB\nfrom sqlalchemy.orm import Session\nfrom datetime import datetime'
)

# Find batch section: from "# ===== BATCH =====" to end of list_batches function
pattern = r'# ===== BATCH =====.*?for k, v in batch_history\.items\(\)\s*\]'
match = re.search(pattern, content, re.DOTALL)

if not match:
    print('ERROR: Could not find batch section in infra.py')
    exit(1)

new_batch = '''# ===== BATCH =====
@router.post("/batch/evaluate", tags=["batch"])
def batch_evaluate(req: BatchRequest, db: Session = Depends(get_db)):
    from app.main import COUNTRIES, calculate_esg

    batch_id = generate_batch_id()
    start = time.time()
    results = []
    success = 0
    fail = 0
    for p in req.projects:
        try:
            project = Project(**{k: v for k, v in p.items() if k in Project.model_fields})
            cdata = COUNTRIES.get(project.region or "Germany", {"region": "Europe", "lat": 50.0, "lon": 10.0})
            region_name = cdata.get("region", "Europe")
            result = calculate_esg(project, region_name)
            result["project_name"] = project.name
            result["status"] = "success"
            results.append(result)
            success += 1
        except Exception as e:
            results.append({"project_name": p.get("name", "unknown"), "status": "error", "error": str(e)})
            fail += 1
    elapsed = round((time.time() - start) * 1000, 2)
    status = "completed" if fail == 0 else ("partial" if success > 0 else "failed")

    import json as _json
    db_record = BatchResultDB(
        batch_id=batch_id,
        created_at=datetime.utcnow(),
        total=len(req.projects),
        successful=success,
        failed=fail,
        duration_ms=elapsed,
        status=status,
        results_json=_json.dumps(results),
        trigger_source="manual",
    )
    db.add(db_record)
    db.commit()

    return {
        "batch_id": batch_id,
        "total": len(req.projects),
        "successful": success,
        "failed": fail,
        "results": results,
        "processing_time_ms": elapsed,
    }


@router.get("/batch/{batch_id}", tags=["batch"])
def get_batch(batch_id: str, db: Session = Depends(get_db)):
    import json as _json
    record = db.query(BatchResultDB).filter(BatchResultDB.batch_id == batch_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Batch not found")
    return {
        "batch_id": record.batch_id,
        "total": record.total,
        "successful": record.successful,
        "failed": record.failed,
        "results": _json.loads(record.results_json) if record.results_json else [],
        "processing_time_ms": record.duration_ms,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
    }


@router.get("/batch", tags=["batch"])
def list_batches(limit: int = 20, db: Session = Depends(get_db)):
    records = (
        db.query(BatchResultDB)
        .order_by(BatchResultDB.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "batch_id": r.batch_id,
            "total": r.total,
            "successful": r.successful,
            "failed": r.failed,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
            "duration_ms": r.duration_ms,
        }
        for r in records
    ]'''

content = content[:match.start()] + new_batch + content[match.end():]
open('app/api/infra.py', 'w').write(content)
print('infra.py batch section replaced')
PYEOF2

echo ""
echo "=== ALL DONE ==="
echo "Now run:"
echo "  pytest tests/ -x -q"
echo "  docker compose up -d --force-recreate app"
echo ""
echo "Then test:"
echo '  curl -s -X POST http://localhost:8000/batch/evaluate -H "Content-Type: application/json" -d '\''{"projects": [{"name": "Test", "budget": 100000, "co2_reduction": 50, "social_impact": 70, "duration_months": 12}]}'\'' | python3 -m json.tool'
echo '  curl -s http://localhost:8000/batch | python3 -m json.tool'
