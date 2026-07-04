"""API-Keys für externen Maschinenzugriff (n8n & Co.) — App-Schicht.

Drei hierarchische Scopes: read < write < admin. Ein Key mit höherem Scope
darf alles, was niedrigere dürfen; admin schließt das Registry-Gate
(propose/approve/reject) ein. Secrets liegen bewusst im Klartext in der DB —
sie sind in der UI jederzeit wieder anzeigbar (Produktentscheidung,
Single-User-Betrieb); die Verwaltung bleibt sessiongebunden.
"""

import secrets
from uuid import UUID

from .errors import NotFoundError, ValidationError

SCOPES = ("read", "write", "admin")
_RANK = {s: i for i, s in enumerate(SCOPES)}

_KEY_COLUMNS = "id, name, secret, scope, created_at, rotated_at, last_used_at"


def scope_covers(have: str, need: str) -> bool:
    """Hierarchie-Check: deckt der Key-Scope den geforderten Scope ab?"""
    return _RANK[have] >= _RANK[need]


def _new_secret() -> str:
    return "wm_" + secrets.token_urlsafe(36)


def create_key(conn, name: str, scope: str) -> dict:
    name = name.strip()
    if not name:
        raise ValidationError("API-Key braucht einen Namen.")
    if scope not in SCOPES:
        raise ValidationError(f"Ungültiger Scope '{scope}' (erlaubt: {', '.join(SCOPES)}).")
    return conn.execute(
        f"""INSERT INTO api_key (name, secret, scope)
            VALUES (%s, %s, %s)
            RETURNING {_KEY_COLUMNS}""",
        (name, _new_secret(), scope),
    ).fetchone()


def list_keys(conn) -> list[dict]:
    return conn.execute(
        f"SELECT {_KEY_COLUMNS} FROM api_key ORDER BY created_at"
    ).fetchall()


def rotate_key(conn, key_id: UUID) -> dict:
    row = conn.execute(
        f"""UPDATE api_key SET secret = %s, rotated_at = now()
            WHERE id = %s
            RETURNING {_KEY_COLUMNS}""",
        (_new_secret(), key_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"API-Key {key_id} existiert nicht.")
    return row


def delete_key(conn, key_id: UUID) -> None:
    row = conn.execute(
        "DELETE FROM api_key WHERE id = %s RETURNING id", (key_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"API-Key {key_id} existiert nicht.")


def resolve_secret(conn, secret: str) -> dict | None:
    """Key-Lookup fürs Auth-Gate; aktualisiert last_used_at als Nebeneffekt."""
    return conn.execute(
        f"""UPDATE api_key SET last_used_at = now()
            WHERE secret = %s
            RETURNING {_KEY_COLUMNS}""",
        (secret,),
    ).fetchone()
