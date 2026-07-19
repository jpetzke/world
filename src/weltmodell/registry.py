"""Type-/Predicate-Registry und Review-Gate (Spec §2, §7.1).

Typen, Interfaces und Prädikate sind Daten, keine DDL. Neue Typen/Prädikate
entstehen NUR über propose_* → approve_* (Invariante 2) — erzwungen hier im
Code, für Menschen- und LLM-Writes gleichermaßen. Approve validiert die
Registry-Regeln: Typ braucht Parent + Interfaces, Prädikat braucht
domain/range/cardinality.
"""

from typing import Any

import psycopg
from psycopg import sql

from .errors import NotFoundError, RegistryError, ValidationError

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


def _id_suggestions(conn: psycopg.Connection, table: str, wrong_id: str) -> list[str]:
    rows = conn.execute(
        sql.SQL(
            """SELECT id FROM {}
               WHERE lower(id) = lower(%(q)s)
                  OR id ILIKE '%%' || %(q)s || '%%'
                  OR %(q)s ILIKE '%%' || id || '%%'
               ORDER BY (lower(id) = lower(%(q)s)) DESC, length(id) LIMIT 3"""
        ).format(sql.Identifier(table)),
        {"q": wrong_id},
    ).fetchall()
    return [r["id"] for r in rows]


def unknown_type_message(conn: psycopg.Connection, type_id: str) -> str:
    """Fehlertext mit Kandidaten: LLM-Aufrufer schicken plausible Varianten
    ("person", "SocialAccount") — ohne Vorschlag kostet jeder Tippfehler einen
    blinden Retry-Roundtrip."""
    msg = f"Unbekannter Typ '{type_id}'"
    hints = _id_suggestions(conn, "entity_type", type_id)
    if hints:
        msg += " — meintest du " + ", ".join(f"'{h}'" for h in hints) + "?"
    return msg


def unknown_predicate_message(conn: psycopg.Connection, predicate_id: str) -> str:
    msg = f"Unbekanntes Prädikat '{predicate_id}'"
    hints = _id_suggestions(conn, "predicate", predicate_id)
    if hints:
        msg += " — meintest du " + ", ".join(f"'{h}'" for h in hints) + "?"
    return msg


def check_predicates(conn: psycopg.Connection, predicates: list[str] | None) -> None:
    """Prädikat-Listen vor Filter-Queries prüfen: ein Tippfehler-Prädikat
    lieferte sonst ein stilles Leerergebnis — für den Aufrufer ununterscheidbar
    von 'diese Fakten existieren nicht'."""
    if not predicates:
        return
    known = {
        r["id"]
        for r in conn.execute(
            "SELECT id FROM predicate WHERE id = ANY(%s)", (predicates,)
        ).fetchall()
    }
    unknown = [p for p in predicates if p not in known]
    if unknown:
        raise ValidationError(
            "; ".join(unknown_predicate_message(conn, p) for p in unknown)
        )


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


def descendant_type_ids(conn: psycopg.Connection, type_id: str) -> list[str]:
    """Typ + alle Subtypen — damit ein Filter auf einen abstrakten Typ (z. B.
    Agent) auch dessen konkrete Subtypen (Person, Organization) findet."""
    return [
        r["id"]
        for r in conn.execute(
            """WITH RECURSIVE down AS (
                 SELECT id FROM entity_type WHERE id = %s
                 UNION ALL
                 SELECT t.id FROM entity_type t JOIN down ON t.parent_id = down.id
               ) SELECT id FROM down""",
            (type_id,),
        ).fetchall()
    ]


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
        "implementations": conn.execute(
            "SELECT type_id, interface_id FROM type_implements ORDER BY type_id"
        ).fetchall(),
    }


# --- Review-Gate: Typen ------------------------------------------------------


def propose_type(
    conn: psycopg.Connection,
    *,
    type_id: str,
    kind: str,
    label: str,
    parent_id: str | None = None,
    interfaces: list[str] | None = None,
    label_predicate: str | None = None,
    abstract: bool = False,
    wikidata_qid: str | None = None,
    rationale: str | None = None,
    proposed_by: str,
) -> dict:
    if get_type(conn, type_id):
        raise RegistryError(f"Typ '{type_id}' existiert bereits")
    if kind not in ("continuant", "occurrent"):
        raise RegistryError(
            f"Ungültiges kind '{kind}' (erlaubt: continuant, occurrent)"
        )
    return conn.execute(
        """INSERT INTO proposed_type
             (type_id, parent_id, kind, label, interfaces, label_predicate,
              abstract, wikidata_qid, rationale, proposed_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (type_id, parent_id, kind, label, interfaces or [], label_predicate,
         abstract, wikidata_qid, rationale, proposed_by),
    ).fetchone()


def approve_type(conn: psycopg.Connection, proposal_id: str) -> dict:
    prop = _pending(conn, "proposed_type", proposal_id)
    # Alle Checks VOR den Inserts: eine RegistryError ist eine Python-Exception,
    # kein DB-Fehler — sie rollt die Transaktion nicht automatisch zurück.
    if prop["parent_id"] is None:
        # Root-Typ: es gibt keinen Parent-Kind-Match — nur das kind-Etikett
        # selbst wird validiert (Invariante 5 beginnt an der Wurzel).
        if prop["kind"] not in ("continuant", "occurrent"):
            raise RegistryError(f"Ungültiges kind '{prop['kind']}'")
    else:
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
    if prop["label_predicate"] is not None:
        lp = get_predicate(conn, prop["label_predicate"])
        if lp is None:
            raise RegistryError(
                f"label_predicate '{prop['label_predicate']}' existiert nicht"
            )
        # Domain-Kompatibilität ohne den (noch nicht existierenden) Typ:
        # Typ-Domains müssen ein Ancestor des Parents sein, Interface-Domains
        # von den eigenen oder geerbten Interfaces abgedeckt.
        ancestors = type_ancestors(conn, prop["parent_id"]) if prop["parent_id"] else []
        ifaces = set(prop["interfaces"])
        if prop["parent_id"]:
            ifaces |= type_interfaces(conn, prop["parent_id"])
        ok = (lp["domain_type"] in ancestors if lp["domain_type"] else False) or (
            lp["domain_interface"] in ifaces if lp["domain_interface"] else False
        )
        if not ok:
            raise RegistryError(
                f"label_predicate '{prop['label_predicate']}' ist nicht "
                f"domain-kompatibel zu '{prop['type_id']}' (erwartet: "
                f"{lp['domain_type'] or lp['domain_interface']})"
            )

    conn.execute(
        """INSERT INTO entity_type
             (id, parent_id, kind, label, abstract, label_predicate, wikidata_qid)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (prop["type_id"], prop["parent_id"], prop["kind"], prop["label"],
         prop["abstract"], prop["label_predicate"], prop["wikidata_qid"]),
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
    identifying: bool = False,
    wikidata_pid: str | None = None,
    schema_org: str | None = None,
    rationale: str | None = None,
    proposed_by: str,
) -> dict:
    if get_predicate(conn, predicate_id):
        raise RegistryError(f"Prädikat '{predicate_id}' existiert bereits")
    # Kontextfreie Shape-Regeln sofort prüfen (fail fast) — Kontext-Regeln
    # (Domain/Range/Parent existieren?) bleiben beim Approve, weil sich der
    # Kontext bis dahin ändern kann (z. B. Typ-Proposal in derselben Charge).
    if range_kind not in RANGE_KINDS:
        raise RegistryError(
            f"Ungültiger range_kind '{range_kind}' (erlaubt: {', '.join(RANGE_KINDS)})"
        )
    if range_type and range_kind != "entity":
        raise RegistryError("range_type nur bei range_kind='entity' erlaubt")
    if identifying and (range_kind != "string" or cardinality != "1:1"):
        raise RegistryError(
            "identifying erfordert range_kind='string' und cardinality='1:1' "
            "(Stufe-1-Resolve matcht exakt auf value_text, §7.2) — ist "
            f"range_kind='{range_kind}', cardinality='{cardinality}'"
        )
    return conn.execute(
        """INSERT INTO proposed_predicate
             (predicate_id, label, domain_type, domain_interface, range_kind,
              range_type, cardinality, inverse_id, identifying, wikidata_pid,
              schema_org, rationale, proposed_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (predicate_id, label, domain_type, domain_interface, range_kind,
         range_type, cardinality, inverse_id, identifying, wikidata_pid,
         schema_org, rationale, proposed_by),
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
    inverse_partner = None  # pending Gegenstück eines Henne-Ei-Paars
    if prop["inverse_id"] and prop["inverse_id"] != prop["predicate_id"] and not get_predicate(conn, prop["inverse_id"]):
        inverse_partner = conn.execute(
            """SELECT id FROM proposed_predicate
               WHERE predicate_id = %s AND status = 'pending'""",
            (prop["inverse_id"],),
        ).fetchone()
        if inverse_partner is None:
            raise RegistryError(
                f"Inverses Prädikat '{prop['inverse_id']}' existiert nicht — "
                "erst anlegen/proposen; liegt es als pending Proposal vor, "
                "wird das Paar beim Approve atomar angelegt"
            )
    if prop["identifying"] and (
        prop["range_kind"] != "string" or prop["cardinality"] != "1:1"
    ):
        raise RegistryError(
            "identifying erfordert range_kind='string' und cardinality='1:1' "
            "(Stufe-1-Resolve matcht exakt auf value_text, §7.2) — ist "
            f"range_kind='{prop['range_kind']}', cardinality='{prop['cardinality']}'"
        )
    if prop["identifying"]:
        # Vor den Inserts: der Konflikt-Check wirft eine RegistryError, und die
        # rollt als Python-Exception die Transaktion nicht automatisch zurück.
        ensure_identifying_index(conn, prop["predicate_id"])

    conn.execute(
        """INSERT INTO predicate
             (id, label, domain_type, domain_interface, range_kind, range_type,
              cardinality, inverse_id, identifying, wikidata_pid, schema_org)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (prop["predicate_id"], prop["label"], prop["domain_type"],
         prop["domain_interface"], prop["range_kind"], prop["range_type"],
         prop["cardinality"],
         prop["predicate_id"] if prop["inverse_id"] == prop["predicate_id"]
         else None if inverse_partner else prop["inverse_id"],
         prop["identifying"], prop["wikidata_pid"], prop["schema_org"]),
    )
    # Gegenrichtung automatisch eintragen (§2.3)
    if prop["inverse_id"] and prop["inverse_id"] != prop["predicate_id"]:
        conn.execute(
            "UPDATE predicate SET inverse_id = %s WHERE id = %s AND inverse_id IS NULL",
            (prop["predicate_id"], prop["inverse_id"]),
        )
    # Henne-Ei-Paar: das pending Gegenstück in derselben Transaktion approven.
    # Unsere Zeile existiert bereits (inverse_id NULL wegen FK), dessen
    # Gegenrichtungs-UPDATE füllt sie; zeigt der Partner woandershin,
    # trägt der Fallback die eigene Deklaration nach.
    if inverse_partner:
        approve_predicate(conn, str(inverse_partner["id"]))
        conn.execute(
            "UPDATE predicate SET inverse_id = %s WHERE id = %s AND inverse_id IS NULL",
            (prop["inverse_id"], prop["predicate_id"]),
        )
    return _decide(conn, "proposed_predicate", proposal_id, "approved")


# --- Review-Gate: Interfaces --------------------------------------------------


def propose_interface(
    conn: psycopg.Connection,
    *,
    interface_id: str,
    label: str,
    rationale: str | None = None,
    proposed_by: str,
) -> dict:
    if conn.execute(
        "SELECT 1 FROM interface WHERE id = %s", (interface_id,)
    ).fetchone():
        raise RegistryError(f"Interface '{interface_id}' existiert bereits")
    return conn.execute(
        """INSERT INTO proposed_interface
             (interface_id, label, rationale, proposed_by)
           VALUES (%s, %s, %s, %s) RETURNING *""",
        (interface_id, label, rationale, proposed_by),
    ).fetchone()


def approve_interface(conn: psycopg.Connection, proposal_id: str) -> dict:
    prop = _pending(conn, "proposed_interface", proposal_id)
    if conn.execute(
        "SELECT 1 FROM interface WHERE id = %s", (prop["interface_id"],)
    ).fetchone():
        raise RegistryError(f"Interface '{prop['interface_id']}' existiert bereits")
    conn.execute(
        "INSERT INTO interface (id, label) VALUES (%s, %s)",
        (prop["interface_id"], prop["label"]),
    )
    return _decide(conn, "proposed_interface", proposal_id, "approved")


def ensure_identifying_index(conn: psycopg.Connection, predicate_id: str) -> None:
    """DB-seitiger Dubletten-Schutz für einen identifying-Key: partieller
    Unique-Index über (predicate_id, value_text) auf aktuellen, nicht-
    deprecated Statements. Bestandsdaten werden vorher geprüft — Konflikte
    werden berichtet (RegistryError mit Liste), nie stumm bereinigt.
    Gegenstück zur Migration 0014: Gate und Migration erfüllen dieselben Regeln.
    """
    dups = conn.execute(
        """SELECT value_text, count(*) AS n FROM statement
           WHERE predicate_id = %s AND system_to IS NULL
             AND rank <> 'deprecated' AND value_text IS NOT NULL
           GROUP BY value_text HAVING count(*) > 1
           ORDER BY value_text""",
        (predicate_id,),
    ).fetchall()
    if dups:
        detail = "; ".join(f"'{d['value_text']}' ({d['n']} Statements)" for d in dups)
        raise RegistryError(
            f"identifying für '{predicate_id}' nicht durchsetzbar — Bestandsdaten "
            f"haben Dubletten: {detail}. Erst kuratieren (welt_merge_entities / "
            "welt_fix_statement), dann erneut."
        )
    conn.execute(
        sql.SQL(
            """CREATE UNIQUE INDEX IF NOT EXISTS {name}
               ON statement (predicate_id, value_text)
               WHERE predicate_id = {pred} AND system_to IS NULL
                 AND rank <> 'deprecated'"""
        ).format(
            name=sql.Identifier(f"statement_ident_{predicate_id}_uniq"),
            pred=sql.Literal(predicate_id),
        )
    )


# --- Gemeinsames -------------------------------------------------------------


def reject_proposal(conn: psycopg.Connection, table: str, proposal_id: str) -> dict:
    if table not in ("proposed_type", "proposed_predicate", "proposed_interface"):
        raise RegistryError(f"Unbekannte Proposal-Tabelle '{table}'")
    _pending(conn, table, proposal_id)
    return _decide(conn, table, proposal_id, "rejected")


def list_proposals(conn: psycopg.Connection, status: str = "pending") -> dict:
    if status not in ("pending", "approved", "rejected"):
        raise RegistryError(
            f"Ungültiger status '{status}' (erlaubt: pending, approved, rejected)"
        )
    return {
        "types": conn.execute(
            "SELECT * FROM proposed_type WHERE status = %s ORDER BY created_at",
            (status,),
        ).fetchall(),
        "predicates": conn.execute(
            "SELECT * FROM proposed_predicate WHERE status = %s ORDER BY created_at",
            (status,),
        ).fetchall(),
        "interfaces": conn.execute(
            "SELECT * FROM proposed_interface WHERE status = %s ORDER BY created_at",
            (status,),
        ).fetchall(),
    }


# Amendbare Felder = die Felder des jeweiligen propose_*-Aufrufs. Status,
# Zeitstempel und proposed_by sind bewusst nicht patchbar.
_AMENDABLE: dict[str, set[str]] = {
    "proposed_type": {"type_id", "parent_id", "kind", "label", "interfaces",
                      "label_predicate", "abstract", "wikidata_qid", "rationale"},
    "proposed_predicate": {"predicate_id", "label", "domain_type",
                           "domain_interface", "range_kind", "range_type",
                           "cardinality", "inverse_id", "identifying",
                           "wikidata_pid", "schema_org", "rationale"},
    "proposed_interface": {"interface_id", "label", "rationale"},
}


def amend_proposal(
    conn: psycopg.Connection, proposal_id: str, patch: dict[str, Any]
) -> dict:
    """Proposal nachschärfen statt neu einreichen. Nur pending/rejected;
    approved ist unveränderlich (in der Registry gelandet). Ein Amend auf
    rejected setzt den Status zurück auf pending (decided_at wird geleert)."""
    if not patch:
        raise RegistryError("Leerer Patch — mindestens ein Feld angeben")
    row, table = None, None
    for t in _AMENDABLE:
        row = conn.execute(
            f"SELECT * FROM {t} WHERE id = %s", (proposal_id,)  # noqa: S608 — table whitelisted
        ).fetchone()
        if row is not None:
            table = t
            break
    if row is None:
        raise NotFoundError(f"Proposal {proposal_id} nicht gefunden")
    if row["status"] == "approved":
        raise RegistryError(
            "Proposal ist approved und damit unveränderlich — neues Proposal "
            "einreichen (die Registry-Zeile existiert bereits)."
        )
    unknown = set(patch) - _AMENDABLE[table]
    if unknown:
        raise RegistryError(
            f"Unbekannte Felder für {table}: {sorted(unknown)} "
            f"(erlaubt: {sorted(_AMENDABLE[table])})"
        )
    sets = ", ".join(f"{k} = %({k})s" for k in patch)  # Keys whitelisted
    return conn.execute(
        f"""UPDATE {table} SET {sets}, status = 'pending', decided_at = NULL
            WHERE id = %(id)s RETURNING *""",  # noqa: S608 — table whitelisted
        {**patch, "id": proposal_id},
    ).fetchone()


def propose_types(
    conn: psycopg.Connection, *, items: list[dict[str, Any]], atomic: bool = True
) -> dict[str, Any]:
    """Bulk-Variante von propose_type (Verhalten analog create_entities)."""
    from .entities import run_bulk

    def one(c: psycopg.Connection, item: dict[str, Any]) -> dict[str, Any]:
        fields = dict(item)
        proposed_by = fields.pop("proposed_by", "mcp-agent")
        row = propose_type(c, proposed_by=proposed_by, **fields)
        return {"id": str(row["id"]), "type_id": row["type_id"]}

    return run_bulk(conn, items, one, atomic=atomic)


def propose_predicates(
    conn: psycopg.Connection, *, items: list[dict[str, Any]], atomic: bool = True
) -> dict[str, Any]:
    """Bulk-Variante von propose_predicate (Verhalten analog create_entities)."""
    from .entities import run_bulk

    def one(c: psycopg.Connection, item: dict[str, Any]) -> dict[str, Any]:
        fields = dict(item)
        proposed_by = fields.pop("proposed_by", "mcp-agent")
        row = propose_predicate(c, proposed_by=proposed_by, **fields)
        return {"id": str(row["id"]), "predicate_id": row["predicate_id"]}

    return run_bulk(conn, items, one, atomic=atomic)


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
