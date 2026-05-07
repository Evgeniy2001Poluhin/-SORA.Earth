from typing import Optional, List, Dict, Union
"""Индексатор кодовой базы SORA.Earth для контекстного поиска."""
from pathlib import Path
from config import SORA_REPO

IGNORE_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules", ".pytest_cache", "mlruns", "memory_store"}
CODE_EXTS = {".py", ".md", ".yml", ".yaml", ".toml", ".cfg", ".env.example", ".sh", ".sql"}
MAX_FILE_SIZE = 50_000


def list_project_files() -> List[Path]:
    files = []
    for p in SORA_REPO.rglob("*"):
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in CODE_EXTS and p.stat().st_size < MAX_FILE_SIZE:
            files.append(p)
    return sorted(files)


def read_file_safe(path: Path, max_lines: int = 200) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()[:max_lines]
        return "\n".join(lines)
    except Exception:
        return ""


def get_project_tree(max_depth: int = 3) -> str:
    result = []
    def _walk(d: Path, depth: int, prefix: str = ""):
        if depth > max_depth or d.name in IGNORE_DIRS:
            return
        entries = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name))
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            result.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                ext = "    " if is_last else "│   "
                _walk(entry, depth + 1, prefix + ext)
    if SORA_REPO.exists():
        result.append(str(SORA_REPO.name) + "/")
        _walk(SORA_REPO, 0)
    return "\n".join(result[:200])


def search_code(query: str, max_results: int = 10) -> List[Dict]:
    results = []
    query_lower = query.lower()
    for f in list_project_files():
        if not f.suffix == ".py":
            continue
        try:
            text = f.read_text(errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if query_lower in line.lower():
                results.append({
                    "file": str(f.relative_to(SORA_REPO)),
                    "line": i,
                    "content": line.strip()[:120],
                })
                if len(results) >= max_results:
                    return results
    return results


def get_file_summary(path: str) -> str:
    full = SORA_REPO / path
    if not full.exists():
        return f"File not found: {path}"
    content = read_file_safe(full, max_lines=300)
    lines = content.splitlines()
    return f"# {path} ({len(lines)} lines)\n\n```python\n{content}\n```"
