"""Entity-Anker (Spec §8: 'entity ist nur ein Identitäts-Anker').

Die Wahrheit liegt in den Statements; label ist denormalisierter Cache,
embedding ist ableitbar (Invariante 1).
"""

import psycopg

from .embeddings import get_embedder
from .errors import NotFoundError, ValidationError
from .registry import get_type


def create_entity(
    conn: psycopg.Connection,
    *,
    type_id: str,
    label: str | None = None,
    embed_text: str | None = None,
) -> dict:
    type_row = get_type(conn, type_id)
    if type_row is None:
        raise ValidationError(
            f"Unbekannter Typ '{type_id}' — neue Typen nur durchs Gate (§7.1)"
        )
    if type_row["abstract"]:
        raise ValidationError(f"Typ '{type_id}' ist abstrakt — konkreten Subtyp wählen")
    embedding = None
    text = embed_text or label
    if text:
        embedding = get_embedder().embed(f"{type_id}: {text}")
    return conn.execute(
        """INSERT INTO entity (type_id, label, embedding)
           VALUES (%s, %s, %s) RETURNING id, type_id, label, merged_into, created_at""",
        (type_id, label, embedding),
    ).fetchone()


def get_entity(conn: psycopg.Connection, entity_id: str) -> dict:
    row = conn.execute(
        """SELECT id, type_id, label, merged_into, created_at
           FROM entity WHERE id = %s""",
        (entity_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Entity {entity_id} nicht gefunden")
    return row


def canonical_id(conn: psycopg.Connection, entity_id: str) -> str:
    """Folgt merged_into-Ketten bis zum kanonischen Anker."""
    current = get_entity(conn, entity_id)
    seen = {str(current["id"])}
    while current["merged_into"] is not None:
        current = get_entity(conn, current["merged_into"])
        if str(current["id"]) in seen:  # defensiv gegen Zyklen
            break
        seen.add(str(current["id"]))
    return str(current["id"])
