"""Session-Persistenz: Chats, Messages, Anker-Cache (PostgreSQL).

Messages liegen im OpenAI-Chat-Format als jsonb, plus "_ui"-Meta für die
Darstellung (Dauer, Digest, Anker-Flag). Vor dem LLM-Call wird "_ui"
gestrippt — die LLM-History ist append-only und damit KV-Cache-freundlich.
"""

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..errors import NotFoundError


def create_session(conn: psycopg.Connection, model: str | None = None) -> dict:
    return conn.execute(
        "INSERT INTO ai_session (model) VALUES (%s) "
        "RETURNING id, title, model, created_at, updated_at",
        (model,),
    ).fetchone()


def list_sessions(conn: psycopg.Connection, limit: int = 100) -> list[dict]:
    return conn.execute(
        """SELECT s.id, s.title, s.model, s.created_at, s.updated_at,
                  (SELECT count(*) FROM ai_message m WHERE m.session_id = s.id)
                  AS message_count
           FROM ai_session s ORDER BY s.updated_at DESC LIMIT %s""",
        (limit,),
    ).fetchall()


def get_session(conn: psycopg.Connection, session_id: str) -> dict:
    row = _session_row(conn, session_id)
    messages = conn.execute(
        "SELECT seq, payload, created_at FROM ai_message "
        "WHERE session_id = %s ORDER BY seq",
        (session_id,),
    ).fetchall()
    row["messages"] = messages
    return row


def _session_row(conn: psycopg.Connection, session_id: str) -> dict:
    try:
        row = conn.execute(
            "SELECT id, title, model, pending, anchors, anchors_sent, "
            "created_at, updated_at FROM ai_session WHERE id = %s",
            (session_id,),
        ).fetchone()
    except psycopg.DataError:
        row = None
    if row is None:
        raise NotFoundError(f"Unbekannte Session '{session_id}'")
    return row


def touch(conn: psycopg.Connection, session_id: str, **fields: Any) -> None:
    """updated_at + optionale Felder (title, model, pending) setzen."""
    sets = ["updated_at = now()"]
    params: list[Any] = []
    for key in ("title", "model"):
        if key in fields:
            sets.append(f"{key} = %s")
            params.append(fields[key])
    if "pending" in fields:
        sets.append("pending = %s")
        params.append(
            Jsonb(fields["pending"]) if fields["pending"] is not None else None
        )
    params.append(session_id)
    conn.execute(
        f"UPDATE ai_session SET {', '.join(sets)} WHERE id = %s", params
    )


def append_message(
    conn: psycopg.Connection, session_id: str, payload: dict[str, Any]
) -> int:
    """Nächste Sequenznummer vergeben (Single-User: kein Race relevant)."""
    row = conn.execute(
        """INSERT INTO ai_message (session_id, seq, payload)
           SELECT %s, coalesce(max(seq), 0) + 1, %s
           FROM ai_message WHERE session_id = %s
           RETURNING seq""",
        (session_id, Jsonb(payload), session_id),
    ).fetchone()
    return row["seq"]


def llm_messages(messages: list[dict]) -> list[dict]:
    """Persistierte Messages → LLM-History ("_ui" gestrippt)."""
    return [
        {k: v for k, v in m["payload"].items() if k != "_ui"} for m in messages
    ]


# --- Anker-Cache ---------------------------------------------------------------


def add_anchors(
    conn: psycopg.Connection, session_id: str, anchors: list[dict]
) -> None:
    """Aufgelöste Entities ({id, label, type_id}) dedupliziert anhängen."""
    if not anchors:
        return
    row = conn.execute(
        "SELECT anchors FROM ai_session WHERE id = %s", (session_id,)
    ).fetchone()
    existing = row["anchors"]
    known = {a["id"] for a in existing}
    fresh = [a for a in anchors if a.get("id") and a["id"] not in known]
    if not fresh:
        return
    conn.execute(
        "UPDATE ai_session SET anchors = %s WHERE id = %s",
        (Jsonb(existing + fresh), session_id),
    )


def unsent_anchors(conn: psycopg.Connection, session_id: str) -> list[dict]:
    row = conn.execute(
        "SELECT anchors, anchors_sent FROM ai_session WHERE id = %s",
        (session_id,),
    ).fetchone()
    return row["anchors"][row["anchors_sent"]:]


def mark_anchors_sent(conn: psycopg.Connection, session_id: str) -> None:
    conn.execute(
        "UPDATE ai_session SET anchors_sent = jsonb_array_length(anchors) "
        "WHERE id = %s",
        (session_id,),
    )
