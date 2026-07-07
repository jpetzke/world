"""Der Write-Path: VALIDATE → COMMIT (Spec §3, §4, §7).

Invarianten, hier im Code erzwungen:
- Kein Fakt ohne Provenance: jedes Statement braucht ≥1 Reference (Inv. 3).
- Kein Write am Registry-Vokabular vorbei: unbekanntes Prädikat = Reject (Inv. 2).
- Überschreibe nie — deprecate: Änderungen schließen die alte Zeile
  transaktionszeitlich (system_to) und legen eine neue an (Inv. 4).
"""

from typing import Any

import psycopg

from .entities import canonical_id, get_entity, refresh_entity_label, run_bulk
from .errors import NotFoundError, ValidationError
from .registry import get_predicate, is_subtype, type_interfaces

VALUE_COLUMNS = (
    "object_id", "value_text", "value_number", "value_unit",
    "value_datetime", "value_geo", "value_json",
)


def normalize_value(value: dict[str, Any]) -> dict[str, Any]:
    """Polymorphen Wert (§3.1) auf typisierte Spalten abbilden."""
    kind = value.get("type")
    cols: dict[str, Any] = dict.fromkeys(VALUE_COLUMNS)
    match kind:
        case "entity":
            cols["object_id"] = value.get("object_id")
            if cols["object_id"] is None:
                raise ValidationError("value_type 'entity' braucht object_id")
        case "string":
            cols["value_text"] = value.get("text")
            if cols["value_text"] is None:
                raise ValidationError("value_type 'string' braucht text")
        case "number":
            cols["value_number"] = value.get("number")
            if cols["value_number"] is None:
                raise ValidationError("value_type 'number' braucht number")
        case "quantity":
            cols["value_number"] = value.get("number")
            cols["value_unit"] = value.get("unit")
            if cols["value_number"] is None or cols["value_unit"] is None:
                raise ValidationError("value_type 'quantity' braucht number + unit")
        case "datetime":
            cols["value_datetime"] = value.get("datetime")
            if cols["value_datetime"] is None:
                raise ValidationError("value_type 'datetime' braucht datetime")
        case "geo":
            lat, lon = value.get("lat"), value.get("lon")
            if lat is None or lon is None:
                raise ValidationError("value_type 'geo' braucht lat + lon")
            cols["value_geo"] = f"SRID=4326;POINT({lon} {lat})"
        case "json":
            import json

            if value.get("json") is None:
                raise ValidationError("value_type 'json' braucht json")
            cols["value_json"] = json.dumps(value["json"])
        case _:
            raise ValidationError(f"Unbekannter value_type '{kind}'")
    return {"value_type": kind, **cols}


def validate_statement(
    conn: psycopg.Connection,
    *,
    subject_id: str,
    predicate_id: str,
    value: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Shape-Check gegen domain/range (§7 VALIDATE): reject oder flag.

    Returns (normalisierte Wert-Spalten, Flags). Harte Verstöße → ValidationError.
    """
    problems: list[str] = []
    flags: list[str] = []

    pred = get_predicate(conn, predicate_id)
    if pred is None:
        raise ValidationError(
            f"Unbekanntes Prädikat '{predicate_id}' — der Extraktor erfindet "
            "keine Prädikate; Vorschlag durchs Gate (§7.1)"
        )

    subject = get_entity(conn, subject_id)

    # Domain-Check: Subjekt-Typ muss passen (Typ-Hierarchie oder Interface)
    dom_type, dom_iface = pred["domain_type"], pred["domain_interface"]
    if dom_type or dom_iface:
        ok = bool(dom_type) and is_subtype(conn, subject["type_id"], dom_type)
        if not ok and dom_iface:
            ok = dom_iface in type_interfaces(conn, subject["type_id"])
        if not ok:
            problems.append(
                f"Domain-Verstoß: '{subject['type_id']}' ist kein zulässiges "
                f"Subjekt für '{predicate_id}' (erwartet: {dom_type or dom_iface})"
            )

    # Range-Check: value_type muss range_kind entsprechen
    cols = normalize_value(value)
    if cols["value_type"] != pred["range_kind"]:
        problems.append(
            f"Range-Verstoß: '{predicate_id}' erwartet value_type "
            f"'{pred['range_kind']}', bekam '{cols['value_type']}'"
        )
    elif cols["value_type"] == "entity":
        obj = get_entity(conn, cols["object_id"])
        if pred["range_type"] and not is_subtype(conn, obj["type_id"], pred["range_type"]):
            problems.append(
                f"Range-Verstoß: Objekt-Typ '{obj['type_id']}' ist kein "
                f"Subtyp von '{pred['range_type']}'"
            )

    if problems:
        raise ValidationError("; ".join(problems), problems)

    # Kardinalität: Konflikt ist Flag, kein Reject (Widersprüche koexistieren, §6)
    if pred["cardinality"] == "1:1":
        existing = conn.execute(
            """SELECT count(*) AS n FROM statement
               WHERE subject_id = %s AND predicate_id = %s
                 AND system_to IS NULL AND rank <> 'deprecated'""",
            (subject_id, predicate_id),
        ).fetchone()
        if existing["n"] > 0:
            flags.append("cardinality_conflict_1:1")

    return cols, flags


def commit_statement(
    conn: psycopg.Connection,
    *,
    subject_id: str,
    predicate_id: str,
    value: dict[str, Any],
    source_ids: list[str],
    rank: str = "normal",
    confidence: float = 1.0,
    origin: str = "asserted",
    valid_from: Any = None,
    valid_to: Any = None,
    qualifiers: list[dict[str, Any]] | None = None,
) -> dict:
    """COMMIT (§7 Stufe 5): Statement + Qualifier + Provenance atomar."""
    if not source_ids:
        raise ValidationError(
            "Kein Fakt ohne Provenance (Invariante 3): ≥1 source_id nötig"
        )
    for sid in source_ids:
        if not conn.execute(
            "SELECT 1 FROM source_document WHERE id = %s", (sid,)
        ).fetchone():
            raise NotFoundError(f"source_document {sid} nicht gefunden")

    subject_id = canonical_id(conn, subject_id)
    if value.get("type") == "entity" and value.get("object_id"):
        value = {**value, "object_id": canonical_id(conn, value["object_id"])}

    cols, flags = validate_statement(
        conn, subject_id=subject_id, predicate_id=predicate_id, value=value
    )

    # identifying-Keys: derselbe Wert auf derselben Entity wird RE-BESTÄTIGT
    # (neue Reference ans bestehende Statement, Snapshot-Philosophie) statt
    # dupliziert — der partielle Unique-Index (0014) machte die Dublette sonst
    # zum DB-Fehler. Derselbe Wert auf einer ANDEREN Entity bleibt ein Fehler
    # (echte Dublette, per Index erzwungen).
    if cols["value_type"] == "string" and get_predicate(conn, predicate_id)["identifying"]:
        existing = conn.execute(
            """SELECT * FROM statement
               WHERE subject_id = %s AND predicate_id = %s AND value_text = %s
                 AND system_to IS NULL AND rank <> 'deprecated'""",
            (subject_id, predicate_id, cols["value_text"]),
        ).fetchone()
        if existing:
            for sid in source_ids:
                conn.execute(
                    """INSERT INTO reference (statement_id, source_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (existing["id"], sid),
                )
            existing["flags"] = ["reconfirmed"]
            return existing

    row = conn.execute(
        """INSERT INTO statement
             (subject_id, predicate_id, value_type, object_id, value_text,
              value_number, value_unit, value_datetime, value_geo, value_json,
              rank, confidence, origin, valid_from, valid_to)
           VALUES (%(subject_id)s, %(predicate_id)s, %(value_type)s,
                   %(object_id)s, %(value_text)s, %(value_number)s,
                   %(value_unit)s, %(value_datetime)s, %(value_geo)s::geography,
                   %(value_json)s::jsonb, %(rank)s, %(confidence)s, %(origin)s,
                   %(valid_from)s, %(valid_to)s)
           RETURNING *""",
        {
            "subject_id": subject_id, "predicate_id": predicate_id, **cols,
            "rank": rank, "confidence": confidence, "origin": origin,
            "valid_from": valid_from, "valid_to": valid_to,
        },
    ).fetchone()

    for q in qualifiers or []:
        _insert_qualifier(conn, str(row["id"]), q)

    for sid in source_ids:
        conn.execute(
            """INSERT INTO reference (statement_id, source_id)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (row["id"], sid),
        )

    # Label-Cache neu ableiten, falls dies das Bezeichner-Prädikat war (Inv. 1)
    refresh_entity_label(conn, subject_id, changed_predicate=predicate_id)

    row["flags"] = flags
    return row


def _insert_qualifier(conn: psycopg.Connection, statement_id: str, q: dict) -> None:
    pred = get_predicate(conn, q["predicate_id"])
    if pred is None:
        raise ValidationError(
            f"Unbekanntes Qualifier-Prädikat '{q['predicate_id']}' (Registry, §2.3)"
        )
    cols = normalize_value(q["value"])
    # Festlegung (Verfassung „Qualifier-Validierung"): Qualifier validieren NUR
    # range_kind — der Domain-Check ist BEWUSST ausgesetzt, kein Zufall des
    # Codepfads. Qualifier nutzen Registry-Prädikate dual (Wikidata-Praxis:
    # P580/beginn hängt als Qualifier an fremden Statements); eine Domain
    # bezieht sich auf das Subjekt eines Haupt-Statements, nicht auf das
    # qualifizierte Statement.
    if cols["value_type"] != pred["range_kind"]:
        raise ValidationError(
            f"Qualifier-Range-Verstoß: '{q['predicate_id']}' erwartet "
            f"value_type '{pred['range_kind']}', bekam '{cols['value_type']}'"
        )
    if cols["value_type"] not in ("entity", "string", "number", "quantity", "datetime"):
        raise ValidationError(
            f"Qualifier unterstützt value_type '{cols['value_type']}' nicht"
        )
    conn.execute(
        """INSERT INTO qualifier
             (statement_id, predicate_id, value_type, value_text, value_number,
              value_unit, value_datetime, object_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (statement_id, q["predicate_id"], cols["value_type"], cols["value_text"],
         cols["value_number"], cols["value_unit"], cols["value_datetime"],
         cols["object_id"]),
    )


def supersede_statement(
    conn: psycopg.Connection,
    statement_id: str,
    *,
    rank: str | None = None,
    valid_to: Any = None,
    confidence: float | None = None,
) -> dict:
    """Bitemporale Änderung (Inv. 4): alte Zeile transaktionszeitlich schließen
    (system_to = now()), neue Zeile mit geänderten Feldern anlegen.
    Qualifier und References werden mitkopiert — nichts geht verloren."""
    old = conn.execute(
        "SELECT * FROM statement WHERE id = %s", (statement_id,)
    ).fetchone()
    if old is None:
        raise NotFoundError(f"Statement {statement_id} nicht gefunden")
    if old["system_to"] is not None:
        raise ValidationError("Statement ist nicht mehr aktuell (system_to gesetzt)")

    conn.execute(
        "UPDATE statement SET system_to = now() WHERE id = %s", (statement_id,)
    )
    new = conn.execute(
        """INSERT INTO statement
             (subject_id, predicate_id, value_type, object_id, value_text,
              value_number, value_unit, value_datetime, value_geo, value_json,
              rank, confidence, origin, valid_from, valid_to)
           SELECT subject_id, predicate_id, value_type, object_id, value_text,
                  value_number, value_unit, value_datetime, value_geo, value_json,
                  %(rank)s, %(confidence)s, origin, valid_from, %(valid_to)s
           FROM statement WHERE id = %(id)s
           RETURNING *""",
        {
            "id": statement_id,
            "rank": rank or old["rank"],
            "confidence": confidence if confidence is not None else old["confidence"],
            "valid_to": valid_to if valid_to is not None else old["valid_to"],
        },
    ).fetchone()
    conn.execute(
        """INSERT INTO qualifier (statement_id, predicate_id, value_type,
                                  value_text, value_number, value_unit,
                                  value_datetime, object_id)
           SELECT %s, predicate_id, value_type, value_text, value_number,
                  value_unit, value_datetime, object_id
           FROM qualifier WHERE statement_id = %s""",
        (new["id"], statement_id),
    )
    conn.execute(
        """INSERT INTO reference (statement_id, source_id)
           SELECT %s, source_id FROM reference WHERE statement_id = %s""",
        (new["id"], statement_id),
    )
    # Rank-/Deprecate-Wechsel am Bezeichner-Prädikat kann die preferred-Wahl
    # verschieben → Label-Cache neu ableiten (Inv. 1). Deckt set_rank + deprecate.
    refresh_entity_label(
        conn, str(new["subject_id"]), changed_predicate=new["predicate_id"]
    )
    return new


def deprecate_statement(
    conn: psycopg.Connection, statement_id: str, *, valid_to: Any = None
) -> dict:
    """Überschreibe nie — deprecate (Invariante 4, §6)."""
    return supersede_statement(conn, statement_id, rank="deprecated", valid_to=valid_to)


def commit_statements(
    conn: psycopg.Connection,
    *,
    items: list[dict[str, Any]],
    atomic: bool = True,
) -> dict[str, Any]:
    """Mehrere Statements in einem Rutsch committen (Bulk). Jedes item hat die
    Felder von commit_statement: subject_id, predicate_id, value, source_ids und
    optional rank, confidence, origin, valid_from, valid_to, qualifiers."""

    def one(c: psycopg.Connection, item: dict[str, Any]) -> dict[str, Any]:
        row = commit_statement(
            c,
            subject_id=item["subject_id"],
            predicate_id=item["predicate_id"],
            value=item["value"],
            source_ids=item["source_ids"],
            rank=item.get("rank", "normal"),
            confidence=item.get("confidence", 1.0),
            origin=item.get("origin", "asserted"),
            valid_from=item.get("valid_from"),
            valid_to=item.get("valid_to"),
            qualifiers=item.get("qualifiers") or [],
        )
        return {"id": str(row["id"]), "flags": row["flags"]}

    return run_bulk(conn, items, one, atomic=atomic)


def fix_statement(
    conn: psycopg.Connection,
    statement_id: str,
    *,
    reason: str,
    delete: bool = False,
    value: dict[str, Any] | None = None,
    rank: str | None = None,
    confidence: float | None = None,
    valid_from: Any = None,
    valid_to: Any = None,
) -> dict[str, Any]:
    """ERRATUM-Eskalationsluke — korrigiert einen Record IN PLACE, bricht damit
    bewusst Invariante 4 (kein Overwrite).

    Abgrenzung: supersede/deprecate bewahren Historie, weil sich die WELT ändert
    oder eine bessere Behauptung dazukommt. fix ist NUR für einen echten FEHLER
    im Record — die Zeile war falsch und hätte so nie existieren dürfen. Kein
    neuer bitemporaler Versionssatz, keine deprecated-Kopie: es wird überschrieben
    (value/rank/confidence/valid_from/valid_to, nur die übergebenen Felder) oder
    mit delete=True samt Qualifiern/Referenzen (ON DELETE CASCADE) ganz entfernt.

    Wirkt auf JEDE Zeile per id — auch bereits transaktionszeitlich geschlossene
    (historische) Statements. reason ist Pflicht (Audit). Ein Wert-Fix wird gegen
    die Registry re-validiert: ein Fix darf nie ein ungültiges Statement erzeugen.
    """
    if not reason or not reason.strip():
        raise ValidationError("fix braucht einen reason (Audit-Pflicht).")

    old = conn.execute(
        "SELECT * FROM statement WHERE id = %s", (statement_id,)
    ).fetchone()
    if old is None:
        raise NotFoundError(f"Statement {statement_id} nicht gefunden")

    subject_id, predicate_id = str(old["subject_id"]), old["predicate_id"]

    if delete:
        conn.execute("DELETE FROM statement WHERE id = %s", (statement_id,))
        refresh_entity_label(conn, subject_id, changed_predicate=predicate_id)
        return {"fixed": str(statement_id), "deleted": True, "reason": reason}

    sets: list[str] = []
    params: dict[str, Any] = {"id": statement_id}
    flags: list[str] = []

    if value is not None:
        if value.get("type") == "entity" and value.get("object_id"):
            value = {**value, "object_id": canonical_id(conn, value["object_id"])}
        cols, flags = validate_statement(
            conn, subject_id=subject_id, predicate_id=predicate_id, value=value
        )
        casts = {"value_geo": "::geography", "value_json": "::jsonb"}
        for col in ("value_type", *VALUE_COLUMNS):
            sets.append(f"{col} = %({col})s{casts.get(col, '')}")
            params[col] = cols[col]
    if rank is not None:
        sets.append("rank = %(rank)s")
        params["rank"] = rank
    if confidence is not None:
        sets.append("confidence = %(confidence)s")
        params["confidence"] = confidence
    if valid_from is not None:
        sets.append("valid_from = %(valid_from)s")
        params["valid_from"] = valid_from
    if valid_to is not None:
        sets.append("valid_to = %(valid_to)s")
        params["valid_to"] = valid_to
    if not sets:
        raise ValidationError(
            "fix ohne Änderung: value/rank/confidence/valid_from/valid_to "
            "angeben oder delete=true."
        )

    row = conn.execute(
        f"UPDATE statement SET {', '.join(sets)} WHERE id = %(id)s RETURNING *",
        params,
    ).fetchone()
    refresh_entity_label(conn, subject_id, changed_predicate=predicate_id)
    row["flags"] = flags
    row["fixed_reason"] = reason
    return row
