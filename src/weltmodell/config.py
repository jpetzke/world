"""Konfiguration über Umgebungsvariablen."""

import os

DEFAULT_DSN = "postgresql://weltmodell:weltmodell@localhost:5433/weltmodell"


def get_dsn() -> str:
    return os.environ.get("WELTMODELL_DSN", DEFAULT_DSN)
