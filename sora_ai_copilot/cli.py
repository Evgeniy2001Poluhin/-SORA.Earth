#!/usr/bin/env python3
"""SORA AI Copilot CLI — генератор контекста, память проекта и roadmap."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from memory import Memory
from agent import ask, build_warmup, build_brief, review_file
from indexer import get_project_tree, search_code
from config import STATE_DIR
from roadmap_generator import write_roadmap

mem = Memory()

if HAS_RICH:
    console = Console()

    def out(text: str, title: str = "") -> None:
        console.print(Panel(Markdown(text), title=title, border_style="cyan"))

    def info(text: str) -> None:
        console.print(f"[green]✓[/green] {text}")
else:
    def out(text: str, title: str = "") -> None:
        if title:
            print(f"\n=== {title} ===")
        print(text)

    def info(text: str) -> None:
        print(f"✓ {text}")


def main() -> None:
    if len(sys.argv) < 2:
        print(
            """
SORA AI Copilot — команды:

  python3 cli.py log "сообщение"         Записать в лог сессии + память
  python3 cli.py brief                   Полный брифинг для вставки в LLM
  python3 cli.py warmup "тема"           Контекст по теме для LLM-сессии
  python3 cli.py ask "вопрос"            Поиск по памяти и коду (offline)
  python3 cli.py review path/to/file.py  Показать содержимое файла
  python3 cli.py search "запрос"         Поиск по коду проекта
  python3 cli.py tree                    Структура проекта
  python3 cli.py status                  Показать active_context
  python3 cli.py context "текст"         Обновить active_context
  python3 cli.py fact key value          Сохранить факт
  python3 cli.py recall key              Вспомнить факт
  python3 cli.py done "что сделал"       Записать + намек на обновление контекста
  python3 cli.py roadmap                 Сгенерировать/обновить ROADMAP.md
"""
        )
        return

    cmd = sys.argv[1]

    if cmd == "log":
        msg = " ".join(sys.argv[2:])
        mem.log_session(msg)
        info(f"Записано: {msg}")

    elif cmd == "brief":
        result = build_brief()
        out(result, title="SORA Briefing")
        try:
            import subprocess
            subprocess.run(["pbcopy"], input=result.encode(), check=True)
            info("Скопировано в буфер обмена (pbcopy)")
        except Exception:
            pass

    elif cmd == "warmup":
        topic = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "general"
        result = build_warmup(topic)
        out(result, title=f"Warmup: {topic}")
        try:
            import subprocess
            subprocess.run(["pbcopy"], input=result.encode(), check=True)
            info("Скопировано в буфер обмена (pbcopy)")
        except Exception:
            pass

    elif cmd == "ask":
        question = " ".join(sys.argv[2:])
        out(ask(question), title="SORA Search")

    elif cmd == "review":
        filepath = sys.argv[2] if len(sys.argv) > 2 else ""
        if not filepath:
            print("Укажи путь: python3 cli.py review app/main.py")
            return
        out(review_file(filepath), title=f"File: {filepath}")

    elif cmd == "search":
        query = " ".join(sys.argv[2:])
        hits = search_code(query)
        if hits:
            for h in hits:
                print(f"  {h['file']}:{h['line']}  {h['content']}")
        else:
            print("Ничего не найдено.")

    elif cmd == "tree":
        print(get_project_tree())

    elif cmd == "status":
        ctx_file = STATE_DIR / "active_context.md"
        if ctx_file.exists():
            out(ctx_file.read_text(), title="Active Context")
        else:
            print("active_context.md пуст.")

    elif cmd == "context":
        text = " ".join(sys.argv[2:])
        ctx_file = STATE_DIR / "active_context.md"
        ctx_file.write_text(text)
        info("active_context обновлён")

    elif cmd == "done":
        msg = " ".join(sys.argv[2:])
        mem.log_session(msg, tags=["done"])
        info(f"Записано: {msg}")
        info('Не забудь обновить контекст: python3 cli.py context "следующий шаг"')

    elif cmd == "fact":
        if len(sys.argv) < 4:
            print("python3 cli.py fact key value")
            return
        key = sys.argv[2]
        value = " ".join(sys.argv[3:])
        mem.set_fact(key, value)
        info(f"Факт: {key} = {value}")

    elif cmd == "recall":
        key = sys.argv[2] if len(sys.argv) > 2 else ""
        val = mem.get_fact(key)
        if val:
            print(f"{key} = {val}")
        else:
            print(f'Факт "{key}" не найден.')

    elif cmd == "roadmap":
        path = write_roadmap()
        info(f"ROADMAP обновлён: {path}")

    else:
        print(f"Неизвестная команда: {cmd}")


if __name__ == "__main__":
    main()