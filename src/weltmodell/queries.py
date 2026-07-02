"""Lese-Seite: Current View, bitemporale Sichten, Traversierung, Suche.

Alles hier ist ableitbar — nie zweite Source of Truth (Invariante 1).
"""

from typing import Any

import psycopg

from .embeddings import get_embedder
from .entities import canonical_id, get_entity
from .errors import NotFoundError


def entity_view(
    conn: psycopg.Connection,
    entity_id: str,
    *,
    system_at: Any = None,
    valid_at: Any = None,
    include_deprecated: bool = False,
) -> dict[str, Any]:
    """Entity + Statements. Beantwortet beide §4-Fragen:

    - valid_at:  „Was war am Datum D über X wahr?"
    - system_at: „Was habe ich am Datum D über X geglaubt?"

    Default: aktuelle Sicht (system_to IS NULL, gültige, nicht-deprecated
    Statements, preferred zuerst) — die „current view" aus §8.
    """
    entity_id = canonical_id(conn, entity_id)
    entity = get_entity(conn, entity_id)

    params: dict[str, Any] = {
        "id": entity_id,
        "system_at": system_at,
        "valid_at": valid_at,
        "include_deprecated": include_deprecated,
    }
    time_filter = """
          AND (
            (%(system_at)s::timestamptz IS NULL AND s.system_to IS NULL)
            OR (%(system_at)s::timestamptz IS NOT NULL
                AND s.system_from <= %(system_at)s
                AND (s.system_to IS NULL OR s.system_to > %(system_at)s))
          )
          AND (%(valid_at)s::timestamptz IS NULL
               OR ((s.valid_from IS NULL OR s.valid_from <= %(valid_at)s)
                   AND (s.valid_to IS NULL OR s.valid_to > %(valid_at)s)))
          AND (%(include_deprecated)s OR s.rank <> 'deprecated')
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

    for s in outgoing:
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

    for s in incoming:
        s.pop("value_geo", None)

    return {"entity": entity, "statements": outgoing, "incoming": incoming}


def traverse(
    conn: psycopg.Connection,
    start_id: str,
    *,
    max_depth: int = 3,
    predicates: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Multi-Hop-Traversierung über Recursive CTE (§0, §10; Apache AGE später).

    Folgt ausgehenden Entity-Kanten der aktuellen Sicht, zyklenfrei.
    """
    start_id = canonical_id(conn, start_id)
    rows = conn.execute(
        """WITH RECURSIVE walk AS (
             SELECT s.object_id AS node_id, 1 AS depth,
                    ARRAY[s.subject_id, s.object_id] AS path,
                    ARRAY[s.predicate_id] AS via
             FROM statement s
             WHERE s.subject_id = %(start)s AND s.value_type = 'entity'
               AND s.system_to IS NULL AND s.rank <> 'deprecated'
               AND (%(preds)s::text[] IS NULL OR s.predicate_id = ANY(%(preds)s))
             UNION ALL
             SELECT s.object_id, w.depth + 1,
                    w.path || s.object_id, w.via || s.predicate_id
             FROM statement s
             JOIN walk w ON s.subject_id = w.node_id
             WHERE w.depth < %(max_depth)s AND s.value_type = 'entity'
               AND s.system_to IS NULL AND s.rank <> 'deprecated'
               AND NOT s.object_id = ANY(w.path)
               AND (%(preds)s::text[] IS NULL OR s.predicate_id = ANY(%(preds)s))
           )
           SELECT w.node_id, w.depth, w.path, w.via,
                  e.label, e.type_id
           FROM walk w JOIN entity e ON e.id = w.node_id
           ORDER BY w.depth, e.label""",
        {"start": start_id, "max_depth": max_depth, "preds": predicates},
    ).fetchall()
    return [
        {
            "entity_id": str(r["node_id"]),
            "label": r["label"],
            "type_id": r["type_id"],
            "depth": r["depth"],
            "path": [str(p) for p in r["path"]],
            "via": list(r["via"]),
        }
        for r in rows
    ]


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
                  (SELECT count(*) FROM reference r WHERE r.source_id = d.id)
                  AS statement_count
           FROM source_document d
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
    return {"source": doc, "statements": statements}


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


def semantic_search(
    conn: psycopg.Connection,
    query: str,
    *,
    type_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """pgvector-Suche über Entity-Embeddings (§0, §7.2) + Label-Fallback."""
    embedding = get_embedder().embed(
        f"{type_id}: {query}" if type_id else query
    )
    rows = conn.execute(
        """SELECT id, label, type_id,
                  1 - (embedding <=> %(emb)s::vector) AS similarity
           FROM entity
           WHERE merged_into IS NULL AND embedding IS NOT NULL
             AND (%(type_id)s::text IS NULL OR type_id = %(type_id)s)
           ORDER BY embedding <=> %(emb)s::vector
           LIMIT %(limit)s""",
        {"emb": embedding, "type_id": type_id, "limit": limit},
    ).fetchall()
    results = [
        {**r, "id": str(r["id"]), "similarity": float(r["similarity"])} for r in rows
    ]
    seen = {r["id"] for r in results}
    for r in conn.execute(
        """SELECT id, label, type_id FROM entity
           WHERE merged_into IS NULL AND label ILIKE %s
             AND (%s::text IS NULL OR type_id = %s)
           LIMIT %s""",
        (f"%{query}%", type_id, type_id, limit),
    ).fetchall():
        if str(r["id"]) not in seen:
            results.append({**r, "id": str(r["id"]), "similarity": None})
    return results
