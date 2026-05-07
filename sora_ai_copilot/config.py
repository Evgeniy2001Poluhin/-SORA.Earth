from typing import Optional, List, Dict, Union
"""Конфигурация SORA AI Copilot."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
NOTES_DIR = BASE_DIR / "notes" / "projects"
SESSIONS_DIR = BASE_DIR / "sessions"
STATE_DIR = BASE_DIR / "state"
MEMORY_DIR = BASE_DIR / "memory_store"
SORA_REPO = Path(os.getenv("SORA_REPO", os.path.expanduser("~/sora_earth_ai_platform")))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("COPILOT_MODEL", "claude-sonnet-4-20250514")

MAX_WARMUP_LINES = 60
MAX_CONTEXT_CHUNKS = 10

for d in [NOTES_DIR, SESSIONS_DIR, STATE_DIR, MEMORY_DIR]:
    d.mkdir(parents=True, exist_ok=True)
