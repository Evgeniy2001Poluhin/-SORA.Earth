"""RAG retriever using ChromaDB 1.5+ + sentence-transformers."""
from __future__ import annotations
import os, json, logging
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
from pathlib import Path
from functools import lru_cache
from typing import List, Dict, Any

import chromadb
from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

_CORPUS_PATH = Path(__file__).resolve().parents[2] / "data" / "rag_corpus" / "seed.json"
_CHROMA_DIR = Path(__file__).resolve().parents[3] / ".chroma"
_COLLECTION = "sora_corpus"
_MODEL_DIR = Path("/app/models/all-MiniLM-L6-v2")
_MODEL_NAME = str(_MODEL_DIR) if _MODEL_DIR.exists() else "sentence-transformers/all-MiniLM-L6-v2"


class RagRetriever:
    def __init__(self) -> None:
        self.client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        self.encoder = SentenceTransformer(_MODEL_NAME)
        self.collection = self._ensure_collection()

    def _ensure_collection(self):
        try:
            col = self.client.get_collection(_COLLECTION)
            if col.count() >= 20:
                log.info("RAG collection ready: %d docs", col.count())
                return col
            self.client.delete_collection(_COLLECTION)
        except Exception:
            pass

        col = self.client.create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        with _CORPUS_PATH.open() as f:
            docs = json.load(f)
        embeddings = self.encoder.encode(
            [d["text"] for d in docs], show_progress_bar=False
        ).tolist()
        col.add(
            ids=[d["id"] for d in docs],
            documents=[d["text"] for d in docs],
            metadatas=[{"title": d["title"], "category": d["category"]} for d in docs],
            embeddings=embeddings,
        )
        log.info("RAG collection seeded with %d docs", len(docs))
        return col

    def search(self, query: str, k: int = 4) -> List[Dict[str, Any]]:
        emb = self.encoder.encode([query], show_progress_bar=False).tolist()
        res = self.collection.query(query_embeddings=emb, n_results=k)
        out: List[Dict[str, Any]] = []
        for i in range(len(res["ids"][0])):
            out.append({
                "id": res["ids"][0][i],
                "title": res["metadatas"][0][i]["title"],
                "category": res["metadatas"][0][i]["category"],
                "text": res["documents"][0][i],
                "score": round(1 - res["distances"][0][i], 4),
            })
        return out


@lru_cache(maxsize=1)
def get_retriever() -> RagRetriever:
    return RagRetriever()
