"""Generischer Snapshot-Import: Preview (read-only) + Commit für beliebige
n:m-Entity-Prädikate. (welt_import_follower_list ist ein dünner Wrapper.)

Snapshot-Philosophie (Verfassung): Quellen sind unvollständig — ein bereits
bekanntes Statement wird nicht dupliziert, sondern per reference auf die neue
Quelle re-bestätigt; Abwesenheit in einem Snapshot ist KEIN Gegenbeweis.

Row-Format: {"type_id"?, "label"?, "identifiers"?: {prädikat: wert},
"statements"?: [{"predicate_id":…, "value":{…}}]} — statements werden nur bei
NEUANLAGE der Ziel-Entity mitcommittet (Attribute aus dem Snapshot). Fremde
Row-Keys bleiben erhalten; die Klassifikation annotiert nur status /
entity_id / statement_id / reason. Rows mit vorgesetztem status='invalid'
(z. B. aus einer Wrapper-Normalisierung) werden durchgereicht, nie verworfen.
"""

from typing import Any

import psycopg

from .entities import canonical_id, get_entity
from .errors import ValidationError
from .pipeline import ingest_document
from .registry import (
    get_predicate,
    is_subtype,
    type_interfaces,
    unknown_predicate_message,
)
from .resolution import VECTOR_AUTO_MATCH_THRESHOLD, get_or_create_entity, resolve
from .statements import commit_statement


def _check(
    conn: psycopg.Connection, predicate_id: str, owner_entity_id: str, direction: str
) -> tuple[dict, str]:
    """Prädikat- und Owner-Validierung. Owner-Seite je Richtung:
    outgoing = Owner ist Subjekt (Domain), incoming = Owner ist Objekt (Range)."""
    if direction not in ("outgoing", "incoming"):
        raise ValidationError(f"Ungültige direction '{direction}'")
    pred = get_predicate(conn, predicate_id)
    if pred is None:
        raise ValidationError(unknown_predicate_message(conn, predicate_id))
    if pred["range_kind"] != "entity" or pred["cardinality"] != "n:m":
        raise ValidationError(
            f"Snapshot-Import braucht ein n:m-Entity-Prädikat — '{predicate_id}' "
            f"ist range_kind='{pred['range_kind']}', "
            f"cardinality='{pred['cardinality']}'"
        )
    owner_id = canonical_id(conn, owner_entity_id)
    owner = get_entity(conn, owner_id)
    if direction == "outgoing":
        dom_type, dom_iface = pred["domain_type"], pred["domain_interface"]
        ok = bool(dom_type) and is_subtype(conn, owner["type_id"], dom_type)
        if not ok and dom_iface:
            ok = dom_iface in type_interfaces(conn, owner["type_id"])
        if (dom_type or dom_iface) and not ok:
            raise ValidationError(
                f"Owner-Typ '{owner['type_id']}' ist kein zulässiges Subjekt "
                f"für '{predicate_id}' (erwartet: {dom_type or dom_iface})"
            )
    elif pred["range_type"] and not is_subtype(
        conn, owner["type_id"], pred["range_type"]
    ):
        raise ValidationError(
            f"Owner-Typ '{owner['type_id']}' ist kein zulässiges Objekt "
            f"für '{predicate_id}' (erwartet: {pred['range_type']})"
        )
    return pred, owner_id


def _lookup_statement(
    conn: psycopg.Connection, predicate_id: str, owner_id: str,
    entity_id: str, direction: str,
) -> str | None:
    subject_id, object_id = (
        (owner_id, entity_id) if direction == "outgoing" else (entity_id, owner_id)
    )
    row = conn.execute(
        """SELECT id FROM statement
           WHERE predicate_id = %s AND subject_id = %s AND object_id = %s
             AND system_to IS NULL AND rank <> 'deprecated'
           LIMIT 1""",
        (predicate_id, subject_id, object_id),
    ).fetchone()
    return str(row["id"]) if row else None


def _classify(
    conn: psycopg.Connection, rows: list[dict[str, Any]], pred: dict,
    owner_id: str, direction: str,
) -> list[dict[str, Any]]:
    """Setzt pro Row status: invalid | new_entity | new_statement | confirmed
    (+ entity_id/statement_id, wo bekannt). Nutzt dieselbe Resolve-Logik wie
    der Commit (get_or_create_entity), damit Preview und Commit nie divergieren."""
    target_default = (
        pred["range_type"] if direction == "outgoing" else pred["domain_type"]
    )
    for row in rows:
        if "status" in row:
            continue
        type_id = row.get("type_id") or target_default
        if type_id is None:
            row.update(status="invalid",
                       reason="type_id fehlt (Prädikat hat keinen Default-Typ)")
            continue
        row["type_id"] = type_id
        res = resolve(conn, type_id=type_id, label=row.get("label"),
                      identifiers=row.get("identifiers") or {})
        entity_id = res["match"]
        if (entity_id is None and res["candidates"]
                and res["candidates"][0]["similarity"] >= VECTOR_AUTO_MATCH_THRESHOLD):
            entity_id = res["candidates"][0]["id"]
        if entity_id is None:
            row["status"] = "new_entity"
        elif entity_id == owner_id:
            row.update(status="invalid", reason="Owner-Entity selbst")
        else:
            stmt = _lookup_statement(conn, pred["id"], owner_id, entity_id, direction)
            if stmt:
                row.update(status="confirmed", entity_id=entity_id,
                           statement_id=stmt)
            else:
                row.update(status="new_statement", entity_id=entity_id)
    return rows


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(rows), "new_entity": 0, "new_statement": 0,
              "confirmed": 0, "invalid": 0}
    for row in rows:
        counts[row["status"]] += 1
    return counts


def preview_snapshot(
    conn: psycopg.Connection,
    *,
    predicate_id: str,
    owner_entity_id: str,
    rows: list[dict[str, Any]],
    direction: str = "outgoing",
) -> dict[str, Any]:
    """Read-only: klassifiziert jede Row gegen den Bestand. Schreibt nichts."""
    pred, owner_id = _check(conn, predicate_id, owner_entity_id, direction)
    classified = _classify(conn, [dict(r) for r in rows], pred, owner_id, direction)
    return {"rows": classified, "summary": _summary(classified)}


def commit_snapshot(
    conn: psycopg.Connection,
    *,
    predicate_id: str,
    owner_entity_id: str,
    rows: list[dict[str, Any]],
    direction: str = "outgoing",
    observed_at: Any = None,
    agent: str = "mcp:snapshot-import",
    activity: str = "snapshot_import",
) -> dict[str, Any]:
    """Eine Quelle für den ganzen Batch; pro Row Ziel-Entity (get-or-create)
    + Statement bzw. Re-Bestätigung per reference auf die neue Quelle."""
    pred, owner_id = _check(conn, predicate_id, owner_entity_id, direction)
    rows = [dict(r) for r in rows]

    doc = ingest_document(
        conn,
        raw={"kind": "snapshot_import", "predicate_id": predicate_id,
             "owner_entity_id": owner_id, "direction": direction,
             "observed_at": str(observed_at) if observed_at else None,
             "rows": [{k: r.get(k) for k in ("type_id", "label", "identifiers")}
                      for r in rows if "status" not in r]},
        activity=activity,
        agent=agent,
        retrieved_at=observed_at,
    )
    sid = str(doc["id"])

    # Frisch klassifizieren (Preview-Daten könnten stale sein).
    classified = _classify(conn, rows, pred, owner_id, direction)

    counts = {"entities_created": 0, "statements_created": 0,
              "statements_confirmed": 0, "skipped_invalid": 0}
    for row in classified:
        if row["status"] == "invalid":
            counts["skipped_invalid"] += 1
            continue

        if row["status"] == "confirmed":
            conn.execute(
                """INSERT INTO reference (statement_id, source_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (row["statement_id"], sid),
            )
            counts["statements_confirmed"] += 1
            continue

        entity_id = row.get("entity_id")
        if entity_id is None:
            entity_id, created = get_or_create_entity(
                conn, type_id=row["type_id"], label=row.get("label"),
                identifiers=row.get("identifiers") or {}, source_ids=[sid],
            )
            if created:
                counts["entities_created"] += 1
                for extra in row.get("statements") or []:
                    commit_statement(
                        conn, subject_id=entity_id,
                        predicate_id=extra["predicate_id"],
                        value=extra["value"], source_ids=[sid],
                    )

        subject_id, object_id = (
            (owner_id, entity_id) if direction == "outgoing"
            else (entity_id, owner_id)
        )
        commit_statement(
            conn, subject_id=subject_id, predicate_id=pred["id"],
            value={"type": "entity", "object_id": object_id},
            source_ids=[sid], valid_from=observed_at,
        )
        counts["statements_created"] += 1

    return {"source_id": sid, **counts}
