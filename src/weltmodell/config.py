"""Konfiguration über Umgebungsvariablen (+ .env im Repo-Root, gitignored)."""

import os
from pathlib import Path

DEFAULT_DSN = "postgresql://weltmodell:weltmodell@localhost:5433/weltmodell"
DEFAULT_LLM_MODEL = "poolside/laguna-xs-2.1:free"

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_dotenv() -> None:
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def get_dsn() -> str:
    return os.environ.get("WELTMODELL_DSN", DEFAULT_DSN)


def get_openrouter_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY")


def get_llm_model() -> str:
    return os.environ.get("WELTMODELL_LLM_MODEL", DEFAULT_LLM_MODEL)
