"""Type-/Predicate-Registry und Review-Gate (Spec §2, §7.1).

Typen, Interfaces und Prädikate sind Daten, keine DDL. Neue Typen/Prädikate
entstehen NUR über propose_* → approve_* (Invariante 2) — erzwungen hier im
Code, für Menschen- und LLM-Writes gleichermaßen. Approve validiert die
Registry-Regeln: Typ braucht Parent + Interfaces, Prädikat braucht
domain/range/cardinality.
"""

from typing import Any

import psycopg

from .errors import NotFoundError, RegistryError

RANGE_KINDS = ("entity", "string", "number", "datetime", "geo", "json", "quantity")


# --- Lookups ---------------------------------------------------------------


def get_type(conn: psycopg.Connection, type_id: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM entity_type WHERE id = %s", (type_id,)
    ).fetchone()


def get_predicate(conn: psycopg.Connection, predicate_id: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM predicate WHERE id = %s", (predicate_id,)
    ).fetchone()


def list_types(conn: psycopg.Connection) -> list[dict]:
    return conn.execute("SELECT * FROM entity_type ORDER BY id").fetchall()


def list_interfaces(conn: psycopg.Connection) -> list[dict]:
    return conn.execute("SELECT * FROM interface ORDER BY id").fetchall()


def list_predicates(conn: psycopg.Connection) -> list[dict]:
    return conn.execute("SELECT * FROM predicate ORDER BY id").fetchall()


def type_ancestors(conn: psycopg.Connection, type_id: str) -> list[str]:
    """Typ-Hierarchie aufwärts, inklusive des Typs selbst."""
    rows = conn.execute(
        """WITH RECURSIVE up AS (
             SELECT id, parent_id FROM entity_type WHERE id = %s
             UNION ALL
             SELECT t.id, t.parent_id FROM entity_type t
             JOIN up ON t.id = up.parent_id
           ) SELECT id FROM up""",
        (type_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def is_subtype(conn: psycopg.Connection, type_id: str, ancestor_id: str) -> bool:
    return ancestor_id in type_ancestors(conn, type_id)


def type_interfaces(conn: psycopg.Connection, type_id: str) -> set[str]:
    """Implementierte Interfaces, inklusive der von Eltern-Typen geerbten."""
    ancestors = type_ancestors(conn, type_id)
    if not ancestors:
        return set()
    rows = conn.execute(
        "SELECT DISTINCT interface_id FROM type_implements WHERE type_id = ANY(%s)",
        (ancestors,),
    ).fetchall()
    return {r["interface_id"] for r in rows}


def vocabulary(conn: psycopg.Connection) -> dict[str, Any]:
    """Das erlaubte Vokabular für den Extraktor (§7.1)."""
    return {
        "types": list_types(conn),
        "interfaces": list_interfaces(conn),
        "predicates": list_predicates(conn),
    }


# --- Review-Gate: Typen ------------------------------------------------------


def propose_type(
    conn: psycopg.Connection,
    *,
    type_id: str,
    parent_id: str,
    kind: str,
    label: str,
    interfaces: list[str] | None = None,
    wikidata_qid: str | None = None,
    rationale: str | None = None,
    proposed_by: str,
) -> dict:
    if get_type(conn, type_id):
        raise RegistryError(f"Typ '{type_id}' existiert bereits")
    return conn.execute(
        """INSERT INTO proposed_type
             (type_id, parent_id, kind, label, interfaces, wikidata_qid,
              rationale, proposed_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (type_id, parent_id, kind, label, interfaces or [], wikidata_qid,
         rationale, proposed_by),
    ).fetchone()


def approve_type(conn: psycopg.Connection, proposal_id: str) -> dict:
    prop = _pending(conn, "proposed_type", proposal_id)
    parent = get_type(conn, prop["parent_id"])
    if parent is None:
        raise RegistryError(f"Parent-Typ '{prop['parent_id']}' existiert nicht")
    if parent["kind"] != prop["kind"]:
        raise RegistryError(
            "Continuant/Occurrent-Split ist heilig (Invariante 5): "
            f"Kind '{prop['kind']}' widerspricht Parent-Kind '{parent['kind']}'"
        )
    if get_type(conn, prop["type_id"]):
        raise RegistryError(f"Typ '{prop['type_id']}' existiert bereits")
    known = {i["id"] for i in list_interfaces(conn)}
    unknown = set(prop["interfaces"]) - known
    if unknown:
        raise RegistryError(f"Unbekannte Interfaces: {sorted(unknown)}")

    conn.execute(
        """INSERT INTO entity_type (id, parent_id, kind, label, wikidata_qid)
           VALUES (%s, %s, %s, %s, %s)""",
        (prop["type_id"], prop["parent_id"], prop["kind"], prop["label"],
         prop["wikidata_qid"]),
    )
    for iface in prop["interfaces"]:
        conn.execute(
            "INSERT INTO type_implements (type_id, interface_id) VALUES (%s, %s)",
            (prop["type_id"], iface),
        )
    return _decide(conn, "proposed_type", proposal_id, "approved")


# --- Review-Gate: Prädikate ---------------------------------------------------


def propose_predicate(
    conn: psycopg.Connection,
    *,
    predicate_id: str,
    label: str,
    range_kind: str,
    domain_type: str | None = None,
    domain_interface: str | None = None,
    range_type: str | None = None,
    cardinality: str | None = None,
    inverse_id: str | None = None,
    wikidata_pid: str | None = None,
    schema_org: str | None = None,
    rationale: str | None = None,
    proposed_by: str,
) -> dict:
    if get_predicate(conn, predicate_id):
        raise RegistryError(f"Prädikat '{predicate_id}' existiert bereits")
    return conn.execute(
        """INSERT INTO proposed_predicate
             (predicate_id, label, domain_type, domain_interface, range_kind,
              range_type, cardinality, inverse_id, wikidata_pid, schema_org,
              rationale, proposed_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (predicate_id, label, domain_type, domain_interface, range_kind,
         range_type, cardinality, inverse_id, wikidata_pid, schema_org,
         rationale, proposed_by),
    ).fetchone()


def approve_predicate(conn: psycopg.Connection, proposal_id: str) -> dict:
    prop = _pending(conn, "proposed_predicate", proposal_id)
    if get_predicate(conn, prop["predicate_id"]):
        raise RegistryError(f"Prädikat '{prop['predicate_id']}' existiert bereits")
    if prop["range_kind"] not in RANGE_KINDS:
        raise RegistryError(f"Ungültiger range_kind '{prop['range_kind']}'")
    if prop["cardinality"] is None:
        raise RegistryError("Prädikat braucht cardinality (Registry-Regel, §7.1)")
    if prop["domain_type"] is None and prop["domain_interface"] is None:
        raise RegistryError("Prädikat braucht domain (Typ oder Interface, §2.3)")
    if prop["domain_type"] and not get_type(conn, prop["domain_type"]):
        raise RegistryError(f"Domain-Typ '{prop['domain_type']}' existiert nicht")
    if prop["domain_interface"] and not conn.execute(
        "SELECT 1 FROM interface WHERE id = %s", (prop["domain_interface"],)
    ).fetchone():
        raise RegistryError(
            f"Domain-Interface '{prop['domain_interface']}' existiert nicht"
        )
    if prop["range_kind"] == "entity":
        if prop["range_type"] and not get_type(conn, prop["range_type"]):
            raise RegistryError(f"Range-Typ '{prop['range_type']}' existiert nicht")
    elif prop["range_type"]:
        raise RegistryError("range_type nur bei range_kind='entity' erlaubt")
    if prop["inverse_id"] and prop["inverse_id"] != prop["predicate_id"] and not get_predicate(conn, prop["inverse_id"]):
        raise RegistryError(f"Inverses Prädikat '{prop['inverse_id']}' existiert nicht")

    conn.execute(
        """INSERT INTO predicate
             (id, label, domain_type, domain_interface, range_kind, range_type,
              cardinality, inverse_id, wikidata_pid, schema_org)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (prop["predicate_id"], prop["label"], prop["domain_type"],
         prop["domain_interface"], prop["range_kind"], prop["range_type"],
         prop["cardinality"],
         prop["predicate_id"] if prop["inverse_id"] == prop["predicate_id"] else prop["inverse_id"],
         prop["wikidata_pid"], prop["schema_org"]),
    )
    # Gegenrichtung automatisch eintragen (§2.3)
    if prop["inverse_id"] and prop["inverse_id"] != prop["predicate_id"]:
        conn.execute(
            "UPDATE predicate SET inverse_id = %s WHERE id = %s AND inverse_id IS NULL",
            (prop["predicate_id"], prop["inverse_id"]),
        )
    return _decide(conn, "proposed_predicate", proposal_id, "approved")


# --- Gemeinsames -------------------------------------------------------------


def reject_proposal(conn: psycopg.Connection, table: str, proposal_id: str) -> dict:
    if table not in ("proposed_type", "proposed_predicate"):
        raise RegistryError(f"Unbekannte Proposal-Tabelle '{table}'")
    _pending(conn, table, proposal_id)
    return _decide(conn, table, proposal_id, "rejected")


def list_proposals(conn: psycopg.Connection, status: str = "pending") -> dict:
    return {
        "types": conn.execute(
            "SELECT * FROM proposed_type WHERE status = %s ORDER BY created_at",
            (status,),
        ).fetchall(),
        "predicates": conn.execute(
            "SELECT * FROM proposed_predicate WHERE status = %s ORDER BY created_at",
            (status,),
        ).fetchall(),
    }


def _pending(conn: psycopg.Connection, table: str, proposal_id: str) -> dict:
    row = conn.execute(
        f"SELECT * FROM {table} WHERE id = %s", (proposal_id,)  # noqa: S608 — table whitelisted
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Proposal {proposal_id} nicht gefunden")
    if row["status"] != "pending":
        raise RegistryError(f"Proposal ist bereits '{row['status']}'")
    return row


def _decide(conn: psycopg.Connection, table: str, proposal_id: str, status: str) -> dict:
    return conn.execute(
        f"""UPDATE {table} SET status = %s, decided_at = now()
            WHERE id = %s RETURNING *""",  # noqa: S608 — table whitelisted
        (status, proposal_id),
    ).fetchone()
