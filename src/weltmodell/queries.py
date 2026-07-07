"""Lese-Seite: Current View, bitemporale Sichten, Traversierung, Suche.

Alles hier ist ableitbar — nie zweite Source of Truth (Invariante 1).
"""

from typing import Any

import psycopg

from .embeddings import get_embedder
from .entities import canonical_id, get_entity
from .errors import NotFoundError, ValidationError
from .registry import descendant_type_ids

# Bitemporaler Filter — EINE Definition für entity_view und query_statements,
# damit sich Zeitreise-Semantik nie zwischen den Sichten unterscheidet:
# - system_at NULL  → aktuelle Sicht (system_to IS NULL)
# - system_at D     → „was glaubte ich am Datum D?"
# - valid_at D      → „was war am Datum D wahr?"
_TIME_FILTER = """
      AND (
        (%(system_at)s::timestamptz IS NULL AND s.system_to IS NULL)
        OR (%(system_at)s::timestamptz IS NOT NULL
            AND s.system_from <= %(system_at)s
            AND (s.system_to IS NULL OR s.system_to > %(system_at)s))
      )
      AND (%(valid_at)s::timestamptz IS NULL
           OR ((s.valid_from IS NULL OR s.valid_from <= %(valid_at)s)
               AND (s.valid_to IS NULL OR s.valid_to > %(valid_at)s)))
"""


def _attach_qualifiers_and_references(conn: psycopg.Connection, statements) -> None:
    """Serialisierung wie in entity_view: Qualifier + Quellen pro Statement."""
    for s in statements:
        s["qualifiers"] = conn.execute(
            "SELECT * FROM qualifier WHERE statement_id = %s", (s["id"],)
        ).fetchall()
        s["references"] = conn.execute(
            """SELECT d.id, d.url, d.activity, d.agent, d.retrieved_at
               FROM reference r JOIN source_document d ON d.id = r.source_id
               WHERE r.statement_id = %s""",
            (s["id"],),
        ).fetchall()
        s.pop("value_geo", None)  # binäres PostGIS-Format; value_geojson liefert die Sicht


def entity_view(
    conn: psycopg.Connection,
    entity_id: str,
    *,
    system_at: Any = None,
    valid_at: Any = None,
    include_deprecated: bool = False,
    min_confidence: float | None = None,
    rank: str | None = None,
) -> dict[str, Any]:
    """Entity + Statements. Beantwortet beide §4-Fragen:

    - valid_at:  „Was war am Datum D über X wahr?"
    - system_at: „Was habe ich am Datum D über X geglaubt?"

    Default: aktuelle Sicht (system_to IS NULL, gültige, nicht-deprecated
    Statements, preferred zuerst) — die „current view" aus §8.
    min_confidence/rank filtern wie in query_statements: confidence >= x,
    rank exakt (ein gesetzter rank ersetzt den deprecated-Ausschluss).
    """
    entity_id = canonical_id(conn, entity_id)
    entity = get_entity(conn, entity_id)

    params: dict[str, Any] = {
        "id": entity_id,
        "system_at": system_at,
        "valid_at": valid_at,
        "include_deprecated": include_deprecated,
        "min_confidence": min_confidence,
        "rank": rank,
    }
    time_filter = f"""{_TIME_FILTER}
          AND ((%(rank)s::text IS NOT NULL AND s.rank = %(rank)s)
               OR (%(rank)s::text IS NULL
                   AND (%(include_deprecated)s OR s.rank <> 'deprecated')))
          AND (%(min_confidence)s::real IS NULL
               OR s.confidence >= %(min_confidence)s)
    """
    statement_sql = f"""
        SELECT s.*, e.label AS object_label, e.type_id AS object_type,
               subj.label AS subject_label, subj.type_id AS subject_type,
               ST_AsGeoJSON(s.value_geo)::jsonb AS value_geojson
        FROM statement s
        LEFT JOIN entity e ON e.id = s.object_id
        LEFT JOIN entity subj ON subj.id = s.subject_id
        WHERE s.{{direction}} = %(id)s {time_filter}
        ORDER BY CASE s.rank WHEN 'preferred' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                 s.confidence DESC, s.system_from
    """

    outgoing = conn.execute(
        statement_sql.format(direction="subject_id"), params
    ).fetchall()
    incoming = conn.execute(
        statement_sql.format(direction="object_id"), params
    ).fetchall()

    _attach_qualifiers_and_references(conn, outgoing)

    for s in incoming:
        s.pop("value_geo", None)

    return {"entity": entity, "statements": outgoing, "incoming": incoming}


def query_statements(
    conn: psycopg.Connection,
    *,
    subject_id: str | None = None,
    predicate_id: str | None = None,
    object_id: str | None = None,
    value_text: str | None = None,
    min_confidence: float | None = None,
    rank: str | None = None,
    valid_at: Any = None,
    system_at: Any = None,
    limit: int = 25,
    offset: int = 0,
    aggregate: str | None = None,
    group_by: str | None = None,
) -> dict[str, Any]:
    """Statement-zentrierte Suche (viertes Standbein neben Search, Entity-View
    und Traverse). Alle Filter optional und kombinierbar; bitemporale Filter
    (_TIME_FILTER) exakt wie in entity_view. rank=None blendet deprecated aus
    (wie die Current View), rank gesetzt filtert exakt.

    aggregate: count | sum | avg — statt der Liste. sum/avg nur über number-
    und quantity-Values, bei quantity nach unit gruppiert. group_by
    (subject | object) gruppiert zusätzlich nach Entity.
    """
    if group_by and not aggregate:
        raise ValidationError("group_by nur zusammen mit aggregate")
    if group_by not in (None, "subject", "object"):
        raise ValidationError(f"Ungültiges group_by '{group_by}'")
    if aggregate not in (None, "count", "sum", "avg"):
        raise ValidationError(f"Ungültiges aggregate '{aggregate}'")

    params: dict[str, Any] = {
        "subject_id": canonical_id(conn, subject_id) if subject_id else None,
        "predicate_id": predicate_id,
        "object_id": canonical_id(conn, object_id) if object_id else None,
        "value_text": value_text,
        "min_confidence": min_confidence,
        "rank": rank,
        "valid_at": valid_at,
        "system_at": system_at,
        "limit": limit,
        "offset": offset,
    }
    where = f"""
        WHERE (%(subject_id)s::uuid IS NULL OR s.subject_id = %(subject_id)s)
          AND (%(predicate_id)s::text IS NULL OR s.predicate_id = %(predicate_id)s)
          AND (%(object_id)s::uuid IS NULL OR s.object_id = %(object_id)s)
          AND (%(value_text)s::text IS NULL OR s.value_text = %(value_text)s)
          AND (%(min_confidence)s::real IS NULL
               OR s.confidence >= %(min_confidence)s)
          AND ((%(rank)s::text IS NULL AND s.rank <> 'deprecated')
               OR s.rank = %(rank)s)
          {_TIME_FILTER}
    """

    if aggregate:
        group_col = {"subject": "s.subject_id", "object": "s.object_id"}.get(group_by)
        group_cols: list[str] = [group_col] if group_col else []
        select_cols = list(group_cols)
        if aggregate == "count":
            agg_expr = "count(*) AS count"
        else:
            # sum/avg sind nur über numerische Werte definiert; quantity wird
            # pro unit gruppiert (EUR und USD summieren sich nicht).
            where += " AND s.value_type IN ('number', 'quantity')"
            group_cols.append("s.value_unit")
            select_cols.append("s.value_unit AS unit")
            agg_expr = f"{aggregate}(s.value_number)::float AS value, count(*) AS n"
        group_sql = f"GROUP BY {', '.join(group_cols)}" if group_cols else ""
        rows = conn.execute(
            f"""SELECT {', '.join((*select_cols, agg_expr))}
                FROM statement s {where} {group_sql}""",
            params,
        ).fetchall()
        if group_col:  # Entity-Label zum Gruppen-Key nachschlagen
            key = "subject_id" if group_by == "subject" else "object_id"
            labels = {
                str(r["id"]): r["label"]
                for r in conn.execute(
                    "SELECT id, label FROM entity WHERE id = ANY(%s)",
                    ([row[key] for row in rows if row[key]],),
                ).fetchall()
            }
            for row in rows:
                row["label"] = labels.get(str(row[key])) if row[key] else None
        if aggregate == "count" and not group_col:
            return {"aggregate": "count", "count": rows[0]["count"]}
        return {"aggregate": aggregate, "group_by": group_by, "groups": rows}

    total = conn.execute(
        f"SELECT count(*) AS n FROM statement s {where}", params
    ).fetchone()["n"]
    statements = conn.execute(
        f"""SELECT s.*, e.label AS object_label, e.type_id AS object_type,
                   subj.label AS subject_label, subj.type_id AS subject_type,
                   ST_AsGeoJSON(s.value_geo)::jsonb AS value_geojson
            FROM statement s
            LEFT JOIN entity e ON e.id = s.object_id
            LEFT JOIN entity subj ON subj.id = s.subject_id
            {where}
            ORDER BY CASE s.rank WHEN 'preferred' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                     s.confidence DESC, s.system_from
            LIMIT %(limit)s OFFSET %(offset)s""",
        params,
    ).fetchall()
    _attach_qualifiers_and_references(conn, statements)
    return {"statements": statements, "total": total}


def entity_timeline(conn: psycopg.Connection, entity_id: str) -> list[dict[str, Any]]:
    """Zeitleiste einer Entity (aktuelle Sicht): echte Ereignisse + abgeleitete
    Meilensteine.

    - ereignis:    Occurrent-Entities, die diese Entity per Entity-Statement
                   referenzieren (◆, klickbar), mit beginn/ende.
    - meilenstein: eigene datetime-Statements (erstellt_am, veröffentlicht_am, …)
                   sowie Wechsel des Label-Prädikats (z. B. Handle) aus der
                   Supersession-Historie — Ereignisse ohne Entity (§4).
    """
    entity_id = canonical_id(conn, entity_id)
    entity = get_entity(conn, entity_id)
    items: list[dict[str, Any]] = []

    events = conn.execute(
        """SELECT subj.id, subj.label, subj.type_id,
                  array_agg(DISTINCT s.predicate_id) AS via,
                  (SELECT b.value_datetime FROM statement b
                   WHERE b.subject_id = subj.id AND b.predicate_id = 'beginn'
                     AND b.system_to IS NULL AND b.rank <> 'deprecated'
                   LIMIT 1) AS beginn,
                  (SELECT b.value_datetime FROM statement b
                   WHERE b.subject_id = subj.id AND b.predicate_id = 'ende'
                     AND b.system_to IS NULL AND b.rank <> 'deprecated'
                   LIMIT 1) AS ende
           FROM statement s
           JOIN entity subj ON subj.id = s.subject_id AND subj.merged_into IS NULL
           JOIN entity_type t ON t.id = subj.type_id AND t.kind = 'occurrent'
           WHERE s.object_id = %(id)s AND s.value_type = 'entity'
             AND s.system_to IS NULL AND s.rank <> 'deprecated'
           GROUP BY subj.id, subj.label, subj.type_id""",
        {"id": entity_id},
    ).fetchall()
    for r in events:
        items.append({
            "kind": "ereignis",
            "entity_id": str(r["id"]),
            "label": r["label"],
            "type_id": r["type_id"],
            "via": list(r["via"]),
            "beginn": r["beginn"],
            "ende": r["ende"],
            "at": r["beginn"],
        })

    milestones = conn.execute(
        """SELECT s.predicate_id, p.label AS predicate_label, s.value_datetime
           FROM statement s JOIN predicate p ON p.id = s.predicate_id
           WHERE s.subject_id = %(id)s AND s.value_type = 'datetime'
             AND s.system_to IS NULL AND s.rank <> 'deprecated'""",
        {"id": entity_id},
    ).fetchall()
    for r in milestones:
        items.append({
            "kind": "meilenstein",
            "predicate_id": r["predicate_id"],
            "predicate_label": r["predicate_label"],
            "at": r["value_datetime"],
            "detail": None,
        })

    label_pred = conn.execute(
        "SELECT label_predicate FROM entity_type WHERE id = %s",
        (entity["type_id"],),
    ).fetchone()["label_predicate"]
    if label_pred:
        # Supersession legt eine offene rank='deprecated'-Kopie an — ohne den
        # Filter erschiene jeder Wechsel doppelt.
        history = conn.execute(
            """SELECT value_text, system_from FROM statement
               WHERE subject_id = %(id)s AND predicate_id = %(pred)s
                 AND rank <> 'deprecated'
               ORDER BY system_from""",
            {"id": entity_id, "pred": label_pred},
        ).fetchall()
        if len(history) > 1:
            pred_label = conn.execute(
                "SELECT label FROM predicate WHERE id = %s", (label_pred,)
            ).fetchone()["label"]
            for prev, cur in zip(history, history[1:]):
                if prev["value_text"] != cur["value_text"]:
                    items.append({
                        "kind": "meilenstein",
                        "predicate_id": label_pred,
                        "predicate_label": pred_label,
                        "at": cur["system_from"],
                        "detail": f"{prev['value_text']} → {cur['value_text']}",
                    })

    dated = sorted((i for i in items if i["at"] is not None), key=lambda i: i["at"])
    undated = [i for i in items if i["at"] is None]
    return dated + undated


def neighborhood(
    conn: psycopg.Connection,
    start_id: str,
    *,
    max_depth: int = 1,
    predicates: list[str] | None = None,
    max_nodes: int = 400,
    min_confidence: float | None = None,
    rank: str | None = None,
) -> dict[str, Any]:
    """Ungerichtete k-Hop-Nachbarschaft als induzierter Teilgraph (§0, §10).

    Kein Pfad-Enumerator: eine Recursive CTE sammelt per BFS die *erreichbaren
    Knoten* (durch UNION dedupliziert und zyklensicher), danach werden ALLE
    Entity-Kanten zwischen diesen Knoten geliefert — Cross-Links inklusive.

    Kanten zählen in beide Richtungen. So zeigt auch ein Knoten mit nur
    *eingehenden* Kanten (z. B. ein viel-gefolgter Account) seine Nachbarschaft,
    statt leer zu bleiben. Bei mehr als max_nodes Treffern werden die nächsten
    Knoten (kleinste Hop-Distanz) behalten; total_nodes nennt die echte Größe.
    min_confidence/rank filtern die Kanten wie in query_statements:
    confidence >= x, rank exakt (ersetzt den deprecated-Ausschluss).
    """
    start_id = canonical_id(conn, start_id)
    edge_filter = """
               AND ((%(rank)s::text IS NOT NULL AND s.rank = %(rank)s)
                    OR (%(rank)s::text IS NULL AND s.rank <> 'deprecated'))
               AND (%(min_confidence)s::real IS NULL
                    OR s.confidence >= %(min_confidence)s)
    """
    params: dict[str, Any] = {
        "start": start_id, "max_depth": max_depth, "preds": predicates,
        "rank": rank, "min_confidence": min_confidence,
    }
    reached = conn.execute(
        f"""WITH RECURSIVE reach(node_id, depth) AS (
             SELECT %(start)s::uuid, 0
             UNION
             SELECT CASE WHEN s.subject_id = r.node_id
                         THEN s.object_id ELSE s.subject_id END,
                    r.depth + 1
             FROM reach r
             JOIN statement s
               ON (s.subject_id = r.node_id OR s.object_id = r.node_id)
             WHERE r.depth < %(max_depth)s AND s.value_type = 'entity'
               AND s.system_to IS NULL
               {edge_filter}
               AND (%(preds)s::text[] IS NULL OR s.predicate_id = ANY(%(preds)s))
           )
           SELECT node_id, min(depth) AS depth
           FROM reach GROUP BY node_id ORDER BY min(depth)""",
        params,
    ).fetchall()
    total = len(reached)
    kept = reached[:max_nodes]
    ids = [r["node_id"] for r in kept]
    depth_by = {str(r["node_id"]): r["depth"] for r in kept}

    nodes = conn.execute(
        """SELECT id, type_id, label,
                  (SELECT count(*) FROM statement s
                   WHERE (s.subject_id = entity.id OR s.object_id = entity.id)
                     AND s.system_to IS NULL AND s.rank <> 'deprecated'
                     AND s.value_type = 'entity') AS degree
           FROM entity WHERE id = ANY(%s)""",
        (ids,),
    ).fetchall()
    for n in nodes:
        n["depth"] = depth_by[str(n["id"])]

    edges = conn.execute(
        f"""SELECT s.id, s.subject_id, s.object_id, s.predicate_id,
                  s.rank, s.confidence
           FROM statement s
           WHERE s.value_type = 'entity' AND s.system_to IS NULL
             {edge_filter}
             AND s.subject_id = ANY(%(ids)s) AND s.object_id = ANY(%(ids)s)
             AND (%(preds)s::text[] IS NULL OR s.predicate_id = ANY(%(preds)s))""",
        {"ids": ids, "preds": predicates, "rank": rank,
         "min_confidence": min_confidence},
    ).fetchall()
    return {
        "nodes": nodes,
        "edges": edges,
        "total_nodes": total,
        "start_id": str(start_id),
    }


def list_entities(
    conn: psycopg.Connection,
    *,
    type_id: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    where = "merged_into IS NULL"
    params: dict[str, Any] = {"limit": limit, "offset": offset,
                              "type_id": type_id, "q": f"%{q}%" if q else None}
    where += " AND (%(type_id)s::text IS NULL OR type_id = %(type_id)s)"
    where += " AND (%(q)s::text IS NULL OR label ILIKE %(q)s)"
    total = conn.execute(
        f"SELECT count(*) AS n FROM entity WHERE {where}", params
    ).fetchone()["n"]
    items = conn.execute(
        f"""SELECT id, type_id, label, created_at,
                   (SELECT count(*) FROM statement s
                    WHERE s.subject_id = entity.id AND s.system_to IS NULL)
                   AS statement_count
            FROM entity WHERE {where}
            ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s""",
        params,
    ).fetchall()
    return {"items": items, "total": total}


def list_sources(
    conn: psycopg.Connection, *, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    total = conn.execute("SELECT count(*) AS n FROM source_document").fetchone()["n"]
    items = conn.execute(
        """SELECT d.id, d.url, d.retrieved_at, d.activity, d.agent,
                  f.filename AS file_name, f.mime AS file_mime,
                  f.size_bytes AS file_size,
                  (SELECT count(*) FROM reference r WHERE r.source_id = d.id)
                  AS statement_count
           FROM source_document d
           LEFT JOIN source_file f ON f.source_id = d.id
           ORDER BY d.retrieved_at DESC NULLS LAST
           LIMIT %s OFFSET %s""",
        (limit, offset),
    ).fetchall()
    return {"items": items, "total": total}


def get_source(conn: psycopg.Connection, source_id: str) -> dict[str, Any]:
    doc = conn.execute(
        "SELECT * FROM source_document WHERE id = %s", (source_id,)
    ).fetchone()
    if doc is None:
        raise NotFoundError(f"source_document {source_id} nicht gefunden")
    statements = conn.execute(
        """SELECT s.id, s.predicate_id, s.rank, s.confidence, s.system_to,
                  s.value_type, s.value_text, s.value_number, s.value_unit,
                  s.value_datetime,
                  subj.label AS subject_label, subj.id AS subject_id,
                  obj.label AS object_label, obj.id AS object_id
           FROM reference r
           JOIN statement s ON s.id = r.statement_id
           JOIN entity subj ON subj.id = s.subject_id
           LEFT JOIN entity obj ON obj.id = s.object_id
           WHERE r.source_id = %s
           ORDER BY s.system_from DESC LIMIT 200""",
        (source_id,),
    ).fetchall()
    file_meta = conn.execute(
        """SELECT filename, mime, size_bytes, sha256, created_at
           FROM source_file WHERE source_id = %s""",
        (source_id,),
    ).fetchone()
    return {"source": doc, "statements": statements, "file": file_meta}


def stats(conn: psycopg.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT
             (SELECT count(*) FROM entity WHERE merged_into IS NULL) AS entities,
             (SELECT count(*) FROM statement WHERE system_to IS NULL) AS statements,
             (SELECT count(*) FROM source_document) AS sources,
             (SELECT count(*) FROM proposed_type WHERE status = 'pending')
             + (SELECT count(*) FROM proposed_predicate WHERE status = 'pending')
               AS pending_proposals"""
    ).fetchone()
    row["by_type"] = conn.execute(
        """SELECT type_id, count(*) AS n FROM entity
           WHERE merged_into IS NULL GROUP BY type_id ORDER BY n DESC"""
    ).fetchall()
    return row


def graph_snapshot(conn: psycopg.Connection, *, max_nodes: int = 400) -> dict[str, Any]:
    """Gesamter Graph der aktuellen Sicht: Knoten + Entity-Kanten.

    Bei mehr Entities als max_nodes werden die zuletzt angelegten geliefert
    (total_nodes zeigt die echte Größe — kein stilles Abschneiden).
    """
    total = conn.execute(
        "SELECT count(*) AS n FROM entity WHERE merged_into IS NULL"
    ).fetchone()["n"]
    # Nach Grad sortiert, nicht nach Alter: sonst fallen genau die Hubs raus,
    # die den Graph zusammenhalten, und der Ausschnitt zeigt lose Punkte.
    # ponytail: korrelierte Subquery über alle Entities; bei >10k Knoten auf
    # materialisierten Grad umstellen.
    nodes = conn.execute(
        """SELECT id, type_id, label,
                  (SELECT count(*) FROM statement s
                   WHERE (s.subject_id = entity.id OR s.object_id = entity.id)
                     AND s.system_to IS NULL AND s.rank <> 'deprecated'
                     AND s.value_type = 'entity') AS degree
           FROM entity WHERE merged_into IS NULL
           ORDER BY degree DESC, created_at DESC LIMIT %s""",
        (max_nodes,),
    ).fetchall()
    ids = [n["id"] for n in nodes]
    edges = conn.execute(
        """SELECT s.id, s.subject_id, s.object_id, s.predicate_id,
                  s.rank, s.confidence
           FROM statement s
           WHERE s.value_type = 'entity' AND s.system_to IS NULL
             AND s.rank <> 'deprecated'
             AND s.subject_id = ANY(%s) AND s.object_id = ANY(%s)""",
        (ids, ids),
    ).fetchall()
    return {"nodes": nodes, "edges": edges, "total_nodes": total}


def semantic_search(
    conn: psycopg.Connection,
    query: str,
    *,
    type_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """pgvector-Suche über Entity-Embeddings (§0, §7.2) + Label-Fallback.

    Der Typ-Filter ist subtyp-fähig: Filtern auf `Agent` liefert Person/Organization.
    """
    types = descendant_type_ids(conn, type_id) if type_id else None
    embedding = get_embedder().embed(query)
    rows = conn.execute(
        """SELECT id, label, type_id,
                  1 - (embedding <=> %(emb)s::vector) AS similarity
           FROM entity
           WHERE merged_into IS NULL AND embedding IS NOT NULL
             AND (%(types)s::text[] IS NULL OR type_id = ANY(%(types)s))
           ORDER BY embedding <=> %(emb)s::vector
           LIMIT %(limit)s""",
        {"emb": embedding, "types": types, "limit": limit},
    ).fetchall()
    results = [
        {**r, "id": str(r["id"]), "similarity": float(r["similarity"])} for r in rows
    ]
    seen = {r["id"] for r in results}
    for r in conn.execute(
        """SELECT id, label, type_id FROM entity
           WHERE merged_into IS NULL AND label ILIKE %(q)s
             AND (%(types)s::text[] IS NULL OR type_id = ANY(%(types)s))
           LIMIT %(limit)s""",
        {"q": f"%{query}%", "types": types, "limit": limit},
    ).fetchall():
        if str(r["id"]) not in seen:
            results.append({**r, "id": str(r["id"]), "similarity": None})
    return results
