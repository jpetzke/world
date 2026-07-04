"""Datenbank-Zugriff und Migrations-Runner.

Eine Source of Truth: PostgreSQL (Invariante 1). Migrationen sind
nummerierte SQL-Dateien in db/migrations/, jede läuft genau einmal
(Tracking in schema_migrations).
"""

from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from .config import get_dsn

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


def get_conn(dsn: str | None = None) -> psycopg.Connection:
    conn = psycopg.connect(dsn or get_dsn(), row_factory=dict_row)
    register_vector(conn)
    return conn


def db():
    """FastAPI-Dependency: eine Verbindung pro Request, Commit bei Erfolg.

    Lebt hier (nicht in api.py), damit auch das Auth-Gate sie nutzen kann,
    ohne einen Importzyklus auth → api zu erzeugen. FastAPI cacht die
    Dependency pro Request — Gate und Route teilen dieselbe Verbindung.
    """
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_migrations(dsn: str | None = None) -> list[str]:
    """Wendet ausstehende Migrationen in Dateireihenfolge an."""
    applied: list[str] = []
    with psycopg.connect(dsn or get_dsn()) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                 filename   text PRIMARY KEY,
                 applied_at timestamptz DEFAULT now()
               )"""
        )
        done = {
            r[0]
            for r in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
            )
            applied.append(path.name)
        conn.commit()
    return applied
