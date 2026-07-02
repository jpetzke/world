"""Original-Datei-Ablage: Binär + Metadaten, 1:1 zum source_document (§5).

Bewusst nur kleine Dokumente (< 5 MB) — als bytea direkt in Postgres.
Extraktion (Datei → Statements) ist NICHT Aufgabe dieses Moduls: hier wird
nur das Original archiviert, damit jede Behauptung auf ihr rohes Dokument
zurückführbar bleibt.
"""

import hashlib
from typing import Any

import psycopg

from .errors import NotFoundError, ValidationError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

# Metadaten-Spalten ohne das (potenziell große) data-Feld.
META_COLUMNS = "source_id, filename, mime, size_bytes, sha256, created_at"


def store_source_file(
    conn: psycopg.Connection,
    *,
    source_id: str,
    filename: str,
    mime: str,
    data: bytes,
) -> dict[str, Any]:
    """Original einer Quelle ablegen (1:1). Ersetzt eine vorhandene Datei.

    Reject bei leerer oder zu großer Datei. Returns Metadaten (ohne data).
    """
    if not data:
        raise ValidationError("Leere Datei — nichts zu speichern")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"Datei zu groß: {len(data)} Bytes (max {MAX_UPLOAD_BYTES})"
        )
    if not conn.execute(
        "SELECT 1 FROM source_document WHERE id = %s", (source_id,)
    ).fetchone():
        raise NotFoundError(f"source_document {source_id} nicht gefunden")

    sha = hashlib.sha256(data).hexdigest()
    return conn.execute(
        f"""INSERT INTO source_file (source_id, filename, mime, size_bytes, sha256, data)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id) DO UPDATE SET
              filename = EXCLUDED.filename, mime = EXCLUDED.mime,
              size_bytes = EXCLUDED.size_bytes, sha256 = EXCLUDED.sha256,
              data = EXCLUDED.data
            RETURNING {META_COLUMNS}""",
        (source_id, filename, mime, len(data), sha, data),
    ).fetchone()


def get_source_file(conn: psycopg.Connection, source_id: str) -> dict[str, Any]:
    """Original inkl. Bytes laden (für Download)."""
    row = conn.execute(
        f"SELECT {META_COLUMNS}, data FROM source_file WHERE source_id = %s",
        (source_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Keine Datei zur Quelle {source_id} gespeichert")
    return row


def file_meta(conn: psycopg.Connection, source_id: str) -> dict[str, Any] | None:
    """Nur Metadaten (ohne Bytes) — None, wenn keine Datei existiert."""
    return conn.execute(
        f"SELECT {META_COLUMNS} FROM source_file WHERE source_id = %s",
        (source_id,),
    ).fetchone()
