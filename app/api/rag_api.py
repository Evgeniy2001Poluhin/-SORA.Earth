from fastapi import APIRouter, Query
from app.services.rag import get_retriever

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])

@router.get("/search")
def search(q: str = Query(..., min_length=3), k: int = 4):
    return {"query": q, "results": get_retriever().search(q, k=k)}
