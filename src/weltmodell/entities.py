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
        concrete = [
            r["id"]
            for r in conn.execute(
                """WITH RECURSIVE down AS (
                     SELECT id FROM entity_type WHERE parent_id = %s
                     UNION ALL
                     SELECT t.id FROM entity_type t JOIN down ON t.parent_id = down.id
                   )
                   SELECT d.id FROM down d
                   JOIN entity_type t ON t.id = d.id
                   WHERE NOT t.abstract ORDER BY d.id""",
                (type_id,),
            ).fetchall()
        ]
        hint = (
            f" — konkrete Subtypen: {', '.join(concrete)}"
            if concrete
            else " und hat noch keine konkreten Subtypen"
        )
        raise ValidationError(f"Typ '{type_id}' ist abstrakt, nicht instanziierbar{hint}")
    embedding = None
    text = embed_text or label
    if text and "Embeddable" in type_interfaces(conn, type_id):
        # Bewusst ohne Typ-Prefix: Embeddings müssen typ-übergreifend
        # vergleichbar sein (resolve/search auf abstrakte Typen wie Agent) —
        # der Typ-Filter ist SQL-Job, nicht Embedding-Job.
        embedding = get_embedder().embed(text)
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
        """SELECT e.type_id, t.label_predicate FROM entity e
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
        # Embedding zieht mit dem Label nach (beide ableitbar, Invariante 1) —
        # sonst findet resolve/search umbenannte oder nachträglich benannte
        # Entities nie. Ein embed_text-Override aus der Anlage wird dabei vom
        # Bezeichner-Statement (SoT) abgelöst.
        embedding = None
        if "Embeddable" in type_interfaces(conn, row["type_id"]):
            embedding = get_embedder().embed(best["value_text"])
        conn.execute(
            "UPDATE entity SET label = %s,"
            " embedding = COALESCE(%s::vector, embedding)"
            " WHERE id = %s",
            (best["value_text"], embedding, str(entity_id)),
        )


def recompute_embeddings(conn: psycopg.Connection) -> int:
    """Alle Entity-Embeddings aus dem Label neu ableiten (Invariante 1:
    Embeddings sind ableitbar und jederzeit neu berechenbar). Nötig nach
    einem Wechsel des Embedding-Schemas oder für Bestandsdaten, deren Label
    sich vor dem Embedding-Refresh in refresh_entity_label geändert hat.

    Aufruf: uv run python -c "from weltmodell.db import get_conn; \\
      from weltmodell.entities import recompute_embeddings; \\
      c = get_conn(); print(recompute_embeddings(c)); c.commit()"
    """
    embedder = get_embedder()
    embeddable = {
        t["id"]
        for t in conn.execute("SELECT id FROM entity_type").fetchall()
        if "Embeddable" in type_interfaces(conn, t["id"])
    }
    n = 0
    for row in conn.execute(
        "SELECT id, label, type_id FROM entity WHERE label IS NOT NULL"
    ).fetchall():
        if row["type_id"] not in embeddable:
            continue
        conn.execute(
            "UPDATE entity SET embedding = %s WHERE id = %s",
            (embedder.embed(row["label"]), row["id"]),
        )
        n += 1
    return n


def fix_entity(conn: psycopg.Connection, entity_id: str, *, reason: str) -> dict:
    """ERRATUM für versehentlich angelegte Anker — Pendant zu fix_statement,
    zweite bewusste Ausnahme von Invariante 4.

    Löscht die Entity NUR, wenn sie null eingehende und null ausgehende
    nicht-deprecated Statements hat — sonst ist sie in Benutzung und der
    richtige Weg ist welt_merge_entities (Dublette) bzw. Kuration. Reste des
    Irrtums (historische/deprecated eigene Zeilen) werden mitgelöscht
    (Qualifier/References via ON DELETE CASCADE). Blockiert, wenn fremde
    Qualifier auf den Anker zeigen oder er Teil einer Merge-Kette ist.
    reason ist Pflicht (Audit, wie bei fix_statement)."""
    if not reason or not reason.strip():
        raise ValidationError("fix braucht einen reason (Audit-Pflicht).")
    entity = get_entity(conn, entity_id)
    if entity["merged_into"] is not None:
        raise ValidationError(
            "Entity ist ein Merge-Redirect (merged_into gesetzt) — Löschen "
            "würde die Merge-Kette brechen."
        )
    active = conn.execute(
        """SELECT count(*) AS n FROM statement
           WHERE (subject_id = %(id)s OR object_id = %(id)s)
             AND system_to IS NULL AND rank <> 'deprecated'""",
        {"id": entity_id},
    ).fetchone()["n"]
    if active:
        raise ValidationError(
            f"Entity hat {active} aktive Statements — kein Erratum. "
            "Dublette? welt_merge_entities führt verlustfrei zusammen."
        )
    qualifier_refs = conn.execute(
        "SELECT count(*) AS n FROM qualifier WHERE object_id = %s", (entity_id,)
    ).fetchone()["n"]
    if qualifier_refs:
        raise ValidationError(
            f"Entity wird von {qualifier_refs} Qualifiern fremder Statements "
            "referenziert — kein Erratum."
        )
    merge_children = conn.execute(
        "SELECT count(*) AS n FROM entity WHERE merged_into = %s", (entity_id,)
    ).fetchone()["n"]
    if merge_children:
        raise ValidationError(
            "Entity ist kanonisches Ziel einer Merge-Kette — kein Erratum."
        )
    removed = conn.execute(
        "DELETE FROM statement WHERE subject_id = %(id)s OR object_id = %(id)s",
        {"id": entity_id},
    ).rowcount
    conn.execute("DELETE FROM entity WHERE id = %s", (entity_id,))
    return {"fixed": str(entity_id), "deleted": True, "reason": reason,
            "statements_removed": removed}


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
