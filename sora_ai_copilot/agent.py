"""SORA AI Copilot — генератор контекста для LLM-сессий."""

from datetime import datetime
from typing import List

from config import STATE_DIR, NOTES_DIR, MAX_WARMUP_LINES, MAX_CONTEXT_CHUNKS
from memory import Memory
from indexer import search_code, get_file_summary

mem = Memory()


def build_warmup(topic: str) -> str:
    parts: List[str] = []

    active_ctx = STATE_DIR / "active_context.md"
    if active_ctx.exists():
        parts.append("## Активный контекст")
        parts.append(active_ctx.read_text()[:800])

    relevant = mem.search(topic, n=MAX_CONTEXT_CHUNKS)
    if relevant:
        parts.append(f"\n## Релевантные записи по \"{topic}\"")
        for chunk in relevant:
            parts.append(f"- {chunk[:200]}")

    code_hits = search_code(topic, max_results=5)
    if code_hits:
        parts.append(f"\n## Код по \"{topic}\"")
        for h in code_hits:
            parts.append(f"- {h['file']}:{h['line']} -> {h['content']}")

    recent = mem.get_recent_logs(days=3)
    if recent:
        parts.append("\n## Последние сессии")
        parts.append(recent[:800])

    result = "\n".join(parts)
    lines = result.splitlines()[:MAX_WARMUP_LINES]
    return "\n".join(lines)


def build_brief() -> str:
    parts: List[str] = [
        "# SORA.Earth — Брифинг для LLM-сессии",
        f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
    ]

    project_notes = NOTES_DIR / "sora_earth.md"
    if project_notes.exists():
        parts.append(project_notes.read_text()[:2500])

    active_ctx = STATE_DIR / "active_context.md"
    if active_ctx.exists():
        parts.append("\n## Текущий фокус")
        parts.append(active_ctx.read_text()[:1000])

    recent = mem.get_recent_logs(days=5)
    if recent:
        parts.append("\n## Логи последних сессий")
        parts.append(recent[:1500])

    return "\n".join(parts)


def ask(question: str) -> str:
    parts: List[str] = ["## Ответ (offline — используй warmup/brief для LLM-сессии)\n"]

    relevant = mem.search(question, n=5)
    if relevant:
        parts.append("Из памяти:")
        for r in relevant:
            parts.append(f"  - {r[:200]}")

    hits = search_code(question, max_results=8)
    if hits:
        parts.append("\nНайдено в коде:")
        for h in hits:
            parts.append(f"  - {h['file']}:{h['line']} -> {h['content']}")

    if len(parts) == 1:
        parts.append("Ничего не найдено. Попробуй другой запрос или добавь данных через `log`.")

    return "\n".join(parts)


def review_file(filepath: str) -> str:
    return get_file_summary(filepath)