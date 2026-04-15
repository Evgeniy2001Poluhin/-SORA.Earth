"""Генератор ROADMAP.md из active_context и done-логов."""

from datetime import datetime
from pathlib import Path

from config import STATE_DIR
from memory import Memory

ROOT = Path(__file__).parent
ROADMAP_FILE = ROOT / "ROADMAP.md"

mem = Memory()


def build_roadmap(days: int = 7) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    active_ctx_path = STATE_DIR / "active_context.md"
    active_ctx = active_ctx_path.read_text().strip() if active_ctx_path.exists() else ""

    recent = mem.get_recent_logs(days=days)
    done_lines = []
    if recent:
        for line in recent.splitlines():
            if "[done]" in line:
                done_lines.append(line.strip())

    lines = [
        "# SORA.Earth — Roadmap",
        "",
        f"_Обновлено: {today}_",
        "",
        "## Текущий фокус",
        "",
        active_ctx or "_Не задано_",
        "",
        "## Сделано (done, последние дни)",
        "",
    ]

    if done_lines:
        for line in done_lines:
            cleaned = line.lstrip("- ").strip()
            lines.append(f"- {cleaned}")
    else:
        lines.append("- Пока нет done-записей за выбранный период.")

    lines.extend(
        [
            "",
            "## Следующие шаги (ручное заполнение)",
            "",
            "- [ ] ...",
            "- [ ] ...",
        ]
    )

    return "\n".join(lines)


def write_roadmap() -> Path:
    content = build_roadmap(days=7)
    ROADMAP_FILE.write_text(content, encoding="utf-8")
    return ROADMAP_FILE


if __name__ == "__main__":
    path = write_roadmap()
    print(f"ROADMAP.md обновлён: {path}")