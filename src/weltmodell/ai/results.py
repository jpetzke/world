"""Result-Store: große Tool-Ergebnisse landen in PostgreSQL statt im Kontext.

Das Modell sieht eine Zusammenfassung (Anzahl, Struktur), die ersten 10
Einträge als Sample und eine Referenz ``ref:<id>`` — compute(code, refs)
löst die Referenz wieder auf und rechnet exakt auf den vollen Daten.
"""

import json
import os
from typing import Any

import psycopg

from ..errors import NotFoundError

DEFAULT_THRESHOLD = 8000
SAMPLE_SIZE = 10


def get_threshold() -> int:
    raw = os.environ.get("WORLDAI_RESULT_THRESHOLD")
    return int(raw) if raw else DEFAULT_THRESHOLD


def _structure_summary(value: Any) -> str:
    if isinstance(value, list):
        item = value[0] if value else None
        shape = (
            f", Einträge sind Objekte mit Keys {sorted(item.keys())}"
            if isinstance(item, dict) else ""
        )
        return f"Liste mit {len(value)} Einträgen{shape}"
    if isinstance(value, dict):
        parts = []
        for key, sub in value.items():
            if isinstance(sub, list):
                parts.append(f"{key}: Liste[{len(sub)}]")
            elif isinstance(sub, dict):
                parts.append(f"{key}: Objekt[{len(sub)} Keys]")
            else:
                parts.append(f"{key}: {type(sub).__name__}")
        return f"Objekt mit Keys {{{', '.join(parts)}}}"
    return f"{type(value).__name__}-Wert"


def _sample(value: Any) -> Any:
    if isinstance(value, list):
        return value[:SAMPLE_SIZE]
    if isinstance(value, dict):
        # Erste Listen-Property samplen (typisch: items/bindings/nodes) —
        # der Rest der Struktur steht in der Summary.
        sampled = {}
        for key, sub in value.items():
            sampled[key] = sub[:SAMPLE_SIZE] if isinstance(sub, list) else sub
        return sampled
    return value


def store_result(
    conn: psycopg.Connection, session_id: str, tool_call_id: str | None, value: Any
) -> dict[str, Any]:
    """Ergebnis ablegen und den Digest zurückgeben, den das Modell sieht."""
    summary = _structure_summary(value)
    row = conn.execute(
        """INSERT INTO ai_result (session_id, tool_call_id, content, summary)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (session_id, tool_call_id, psycopg.types.json.Jsonb(value), summary),
    ).fetchone()
    return {
        "offloaded": True,
        "ref": f"ref:{row['id']}",
        "summary": summary,
        "sample": _sample(value),
        "hint": "Volle Daten per compute(code, refs) verrechnen — nicht schätzen.",
    }


def offload_if_large(
    conn: psycopg.Connection,
    session_id: str,
    tool_call_id: str | None,
    value: Any,
    threshold: int | None = None,
) -> tuple[Any, bool]:
    """(modell-sichtbares Ergebnis, wurde offgeloadet?)."""
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if len(serialized) <= (threshold if threshold is not None else get_threshold()):
        return value, False
    return store_result(conn, session_id, tool_call_id, value), True


def resolve_ref(conn: psycopg.Connection, session_id: str, ref: str) -> Any:
    """``ref:<uuid>`` → volles Ergebnis (nur innerhalb derselben Session)."""
    result_id = ref.removeprefix("ref:")
    try:
        row = conn.execute(
            "SELECT content FROM ai_result WHERE id = %s AND session_id = %s",
            (result_id, session_id),
        ).fetchone()
    except psycopg.DataError:
        row = None
    if row is None:
        raise NotFoundError(f"Unbekannte Referenz '{ref}'")
    return row["content"]
