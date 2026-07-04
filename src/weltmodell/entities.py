"""Entity-Anker (Spec §8: 'entity ist nur ein Identitäts-Anker').

Die Wahrheit liegt in den Statements; label ist denormalisierter Cache,
embedding ist ableitbar (Invariante 1).
"""

from typing import Any, Callable

import psycopg

from .embeddings import get_embedder
from .errors import NotFoundError, ValidationError, WeltmodellError
from .registry import get_type, type_interfaces


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
    if text and "Embeddable" in type_interfaces(conn, type_id):
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


def refresh_entity_label(
    conn: psycopg.Connection,
    entity_id: str,
    *,
    changed_predicate: str | None = None,
) -> None:
    """Denormalisierten label-Cache aus dem aktuell besten label_predicate-
    Statement neu berechnen (Invariante 1: label ist ableitbar, jederzeit neu
    berechenbar). Nach jedem Write, der das Bezeichner-Prädikat berührt
    (commit, supersede, set_rank, deprecate, fix), aufrufen.

    changed_predicate ist der Cheap-Guard fürs den Write-Path: ist es gesetzt
    und NICHT das label_predicate des Typs, passiert nichts. „Bester" Bezeichner
    = aktuelle Sicht (system_to IS NULL, nicht deprecated), preferred vor normal,
    dann höchste Confidence, dann jüngster. Fehlt jeder gültige Bezeichner
    (z. B. alle deprecated), bleibt der bisherige Cache stehen — kein Datenverlust.
    """
    row = conn.execute(
        """SELECT t.label_predicate FROM entity e
           JOIN entity_type t ON t.id = e.type_id WHERE e.id = %s""",
        (str(entity_id),),
    ).fetchone()
    if row is None or not row["label_predicate"]:
        return
    label_pred = row["label_predicate"]
    if changed_predicate is not None and changed_predicate != label_pred:
        return
    best = conn.execute(
        """SELECT value_text FROM statement
           WHERE subject_id = %s AND predicate_id = %s
             AND system_to IS NULL AND rank <> 'deprecated'
             AND value_text IS NOT NULL
           ORDER BY CASE rank WHEN 'preferred' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                    confidence DESC, system_from DESC
           LIMIT 1""",
        (str(entity_id), label_pred),
    ).fetchone()
    if best is not None:
        conn.execute(
            "UPDATE entity SET label = %s WHERE id = %s",
            (best["value_text"], str(entity_id)),
        )


def run_bulk(
    conn: psycopg.Connection,
    items: list[dict[str, Any]],
    do_one: Callable[[psycopg.Connection, dict[str, Any]], dict[str, Any]],
    *,
    atomic: bool = True,
) -> dict[str, Any]:
    """Bulk-Ausführung mit Per-Item-Report. do_one committet EIN Item und liefert
    dessen (bereits kompaktes) Ergebnis-Dict.

    atomic=True (Default): alles-oder-nichts — der erste Fehler bricht die ganze
    Transaktion ab (die äußere _tx rollt zurück), der Fehler nennt den Index.
    atomic=False: Best-Effort per SAVEPOINT — ein fehlerhaftes Item rollt nur sich
    selbst zurück, die gültigen bleiben; jedes Item bekommt ok/error im Report.
    """
    results: list[dict[str, Any]] = []
    ok = 0
    for i, item in enumerate(items):
        if atomic:
            try:
                out = do_one(conn, item)
            except WeltmodellError as exc:
                raise ValidationError(f"Item {i}: {exc}") from exc
            ok += 1
            results.append({"index": i, "ok": True, **out})
            continue
        conn.execute(f"SAVEPOINT bulk_{i}")
        try:
            out = do_one(conn, item)
        except WeltmodellError as exc:
            conn.execute(f"ROLLBACK TO SAVEPOINT bulk_{i}")
            results.append({"index": i, "ok": False, "error": str(exc)})
            continue
        conn.execute(f"RELEASE SAVEPOINT bulk_{i}")
        ok += 1
        results.append({"index": i, "ok": True, **out})
    return {"total": len(items), "committed": ok, "results": results}


def create_entities(
    conn: psycopg.Connection,
    *,
    items: list[dict[str, Any]],
    atomic: bool = True,
) -> dict[str, Any]:
    """Mehrere Entities in einem Rutsch anlegen (Bulk). Jedes item:
    {"type_id":…, "label"?:…, "embed_text"?:…}."""

    def one(c: psycopg.Connection, item: dict[str, Any]) -> dict[str, Any]:
        ent = create_entity(
            c, type_id=item["type_id"], label=item.get("label"),
            embed_text=item.get("embed_text"),
        )
        return {"id": str(ent["id"]), "type_id": ent["type_id"], "label": ent["label"]}

    return run_bulk(conn, items, one, atomic=atomic)
