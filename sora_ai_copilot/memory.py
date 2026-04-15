"""Memory layer: ChromaDB для semantic search + JSON для structured facts."""
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

try:
    import chromadb
    from chromadb.config import Settings
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False

from config import MEMORY_DIR, SESSIONS_DIR


class Memory:
    def __init__(self):
        self.facts_file = MEMORY_DIR / "facts.json"
        self.facts = self._load_facts()
        if HAS_CHROMA:
            self.client = chromadb.PersistentClient(
                path=str(MEMORY_DIR / "chroma"),
                settings=Settings(anonymized_telemetry=False),
            )
            self.collection = self.client.get_or_create_collection(
                "sora_memory",
                metadata={"hnsw:space": "cosine"},
            )
        else:
            self.client = None
            self.collection = None

    # --- Facts (structured key-value) ---
    def _load_facts(self) -> dict:
        if self.facts_file.exists():
            return json.loads(self.facts_file.read_text())
        return {}

    def set_fact(self, key: str, value: str):
        self.facts[key] = {"value": value, "updated": datetime.now().isoformat()}
        self.facts_file.write_text(json.dumps(self.facts, indent=2, ensure_ascii=False))

    def get_fact(self, key: str) -> Optional[str]:
        f = self.facts.get(key)
        return f["value"] if f else None

    # --- Semantic memory (ChromaDB) ---
    def add(self, text: str, metadata: Optional[dict] = None):
        if not self.collection:
            return
        doc_id = f"mem_{int(time.time()*1000)}"
        meta = metadata or {}
        meta["timestamp"] = datetime.now().isoformat()
        self.collection.add(documents=[text], metadatas=[meta], ids=[doc_id])

    def search(self, query: str, n: int = 5) -> List[str]:
        if not self.collection or self.collection.count() == 0:
            return []
        results = self.collection.query(query_texts=[query], n_results=min(n, self.collection.count()))
        return results["documents"][0] if results["documents"] else []

    # --- Session logs ---
    def log_session(self, message: str, tags: Optional[List[str]] = None):
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = SESSIONS_DIR / f"{today}.md"
        ts = datetime.now().strftime("%H:%M")
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        entry = f"- **{ts}**{tag_str}: {message}\n"

        with open(log_file, "a") as f:
            if not log_file.exists() or log_file.stat().st_size == 0:
                f.write(f"# Session Log — {today}\n\n")
            f.write(entry)

        self.add(message, {"type": "session_log", "tags": ",".join(tags or [])})

    def get_recent_logs(self, days: int = 3) -> str:
        lines = []
        for f in sorted(SESSIONS_DIR.glob("*.md"), reverse=True)[:days]:
            lines.append(f.read_text())
        return "\n".join(lines)
