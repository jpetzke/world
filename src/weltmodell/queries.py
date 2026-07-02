"""Lese-Seite: Current View, bitemporale Sichten, Traversierung, Suche.

Alles hier ist ableitbar — nie zweite Source of Truth (Invariante 1).
"""

from typing import Any

import psycopg

from .embeddings import get_embedder
from .entities import canonical_id, get_entity


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
               ST_AsGeoJSON(s.value_geo)::jsonb AS value_geojson
        FROM statement s
        LEFT JOIN entity e ON e.id = s.object_id
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
