# SORA AI Copilot

Локальный ИИ-тиммейт с долговременной памятью для проекта SORA.Earth.

## Быстрый старт

```bash
# 1. Установи зависимости
pip install anthropic chromadb click rich

# 2. Настрой API-ключ
export ANTHROPIC_API_KEY="sk-ant-..."

# 3. Логируй сессию
python cli.py log "Починил singleton APScheduler, тест test_refresh проходит"

# 4. Получи warmup для новой сессии
python cli.py warmup "data refresh timeout"

# 5. Спроси тиммейта
python cli.py ask "какие баги сейчас открыты в data pipeline?"

# 6. Code review
python cli.py review path/to/changed_file.py
```

## Структура

```
sora_ai_copilot/
├── cli.py                  # Главный CLI
├── agent.py                # Claude-агент с памятью
├── memory.py               # Memory layer (ChromaDB + JSON)
├── indexer.py              # Индексатор кодовой базы
├── config.py               # Настройки
├── notes/
│   └── projects/
│       └── sora_earth.md   # Опорный документ проекта
├── sessions/               # Логи сессий (авто)
├── state/
│   └── active_context.md   # Текущий фокус
└── memory_store/           # ChromaDB persistence
```
