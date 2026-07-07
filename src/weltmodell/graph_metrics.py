"""Abgeleitete Graph-Metriken: Community, PageRank, Skeleton, Layout-Cache.

Alles hier ist reiner, neu berechenbarer Cache über den aktuellen
Entity-Statements (Invariante 1) — nie eine zweite Wahrheit. Das Frontend
rendert nie den ganzen Bestand, sondern ein repräsentatives Skeleton
(Top-PageRank pro Leiden-Community + globale Hubs) plus fokusgetriebene
Expansion über den bestehenden /query/traverse-Pfad.
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import igraph as ig
import psycopg

from .db import get_conn
from .entities import canonical_id

# Recompute darf pro Prozess nur einmal gleichzeitig laufen (Stampede-Schutz
# für den Lazy-Refresh im Skeleton-Endpoint).
_recompute_lock = threading.Lock()

STALE_AFTER = timedelta(hours=24)

# Aktuelle Entity-Kanten der Current View — dieselben Filter wie
# queries.graph_snapshot, plus Merge-Bereinigung beider Endpunkte.
_EDGES_SQL = """
    SELECT s.subject_id, s.object_id
    FROM statement s
    JOIN entity a ON a.id = s.subject_id AND a.merged_into IS NULL
    JOIN entity b ON b.id = s.object_id AND b.merged_into IS NULL
    WHERE s.value_type = 'entity' AND s.system_to IS NULL
      AND s.rank <> 'deprecated'
"""


def recompute(conn: psycopg.Connection) -> dict[str, Any]:
    """Leiden-Communities + PageRank + Grad für alle aktuellen Entities.

    Multigraph bleibt Multigraph: Grad zählt Statements (wie graph_snapshot),
    damit der Client Ghost-Badges als degree − geladene Kanten rechnen kann.
    x/y werden bewusst NICHT angefasst — die kommen vom Client (save_positions)
    und überleben jeden Recompute (stabile mentale Karte, R4).
    """
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM entity WHERE merged_into IS NULL"
    ).fetchall()]
    index = {eid: i for i, eid in enumerate(ids)}
    edges = [
        (index[r["subject_id"]], index[r["object_id"]])
        for r in conn.execute(_EDGES_SQL).fetchall()
    ]

    if ids:
        g = ig.Graph(n=len(ids), edges=edges, directed=False)
        degree = g.degree()
        # Leiden braucht schleifenfreie Graphen; Mehrfachkanten sind ok und
        # gewichten die Beziehung implizit.
        g_no_loops = g.copy()
        g_no_loops.simplify(multiple=False, loops=True)
        communities = g_no_loops.community_leiden(
            objective_function="modularity", n_iterations=3
        ).membership
        pagerank = g.pagerank()
    else:
        degree, communities, pagerank = [], [], []

    conn.execute("DELETE FROM graph_metrics WHERE entity_id <> ALL(%s)", (ids,))
    conn.execute(
        """INSERT INTO graph_metrics (entity_id, community, pagerank, degree, metrics_at)
           SELECT u.entity_id, u.community, u.pagerank, u.degree, now()
           FROM unnest(%s::uuid[], %s::int[], %s::real[], %s::int[])
                AS u(entity_id, community, pagerank, degree)
           ON CONFLICT (entity_id) DO UPDATE SET
             community = EXCLUDED.community,
             pagerank  = EXCLUDED.pagerank,
             degree    = EXCLUDED.degree,
             metrics_at = EXCLUDED.metrics_at""",
        (ids, list(communities), pagerank, degree),
    )
    # Rang pro Community einmal beim Recompute — die Skeleton-Query zur
    # Request-Zeit liest ihn dann nur noch über den Index.
    conn.execute(
        """UPDATE graph_metrics gm SET community_rank = r.rn
           FROM (SELECT entity_id,
                        row_number() OVER (PARTITION BY community
                                           ORDER BY pagerank DESC) AS rn
                 FROM graph_metrics) r
           WHERE gm.entity_id = r.entity_id"""
    )
    return {
        "entities": len(ids),
        "edges": len(edges),
        "communities": len(set(communities)),
    }


def _refresh_in_background() -> None:
    """Lazy-Nacht-Job: Recompute in eigenem Thread mit eigener Verbindung."""
    if not _recompute_lock.acquire(blocking=False):
        return

    def run() -> None:
        try:
            conn = get_conn()
            try:
                recompute(conn)
                conn.commit()
            finally:
                conn.close()
        finally:
            _recompute_lock.release()

    threading.Thread(target=run, name="graph-metrics-refresh", daemon=True).start()


def skeleton(
    conn: psycopg.Connection, *, budget: int = 800, per_community: int = 3,
    global_hubs: int = 50,
) -> dict[str, Any]:
    """Repräsentatives Grundgerüst: jede Region vertreten, nicht nur das
    dichteste Ego-Netz.

    Auswahl: pro Community die per_community PageRank-stärksten Nodes
    (Ranking `rn, pagerank` — jede Community bekommt ihren ersten Pick, bevor
    irgendeine ihren zweiten bekommt), vereinigt mit den global_hubs stärksten
    Hubs. budget deckelt hart. Erster Aufruf auf leerem Cache rechnet synchron;
    ältere Stände als STALE_AFTER werden im Hintergrund aufgefrischt und der
    aktuelle Stand sofort serviert.
    """
    _STATE_SQL = """
        SELECT (SELECT count(*) FROM entity WHERE merged_into IS NULL) AS total,
               (SELECT max(metrics_at) FROM graph_metrics) AS at"""
    state = conn.execute(_STATE_SQL).fetchone()
    # at IS NULL deckt auch "nur Positionen, nie Metriken" ab (save_positions
    # kann vor dem ersten Recompute laufen).
    if state["total"] and state["at"] is None:
        with _recompute_lock:
            recompute(conn)
        state = conn.execute(_STATE_SQL).fetchone()
    elif state["at"] and datetime.now(timezone.utc) - state["at"] > STALE_AFTER:
        _refresh_in_background()

    rows = conn.execute(
        """WITH picks AS (
             (SELECT entity_id FROM graph_metrics
              WHERE community_rank <= %(per_comm)s
              ORDER BY community_rank, pagerank DESC LIMIT %(budget)s)
             UNION
             (SELECT entity_id FROM graph_metrics
              ORDER BY pagerank DESC LIMIT %(hubs)s)
           )
           SELECT e.id, e.type_id, e.label,
                  m.degree, m.community, m.pagerank, m.x, m.y
           FROM picks p
           JOIN entity e ON e.id = p.entity_id
           JOIN graph_metrics m ON m.entity_id = p.entity_id
           ORDER BY m.pagerank DESC LIMIT %(budget)s""",
        {"per_comm": per_community, "budget": budget, "hubs": global_hubs},
    ).fetchall()
    ids = [r["id"] for r in rows]
    edge_rows = conn.execute(
        """SELECT s.id, s.subject_id, s.object_id, s.predicate_id, s.rank, s.confidence
           FROM statement s
           WHERE s.value_type = 'entity' AND s.system_to IS NULL
             AND s.rank <> 'deprecated'
             AND s.subject_id = ANY(%s) AND s.object_id = ANY(%s)""",
        (ids, ids),
    ).fetchall() if ids else []
    return {
        "nodes": rows,
        "edges": edge_rows,
        "total_nodes": state["total"],
        "metrics_at": state["at"],
    }


def save_positions(
    conn: psycopg.Connection, positions: list[dict[str, Any]]
) -> dict[str, int]:
    """Konvergierte Client-Positionen persistieren (Layout-Persistenz, R4).

    Upsert: auch Entities ohne Metrik-Zeile (seit letztem Recompute neu)
    bekommen ihre Position — der nächste Recompute füllt die Metriken nach.
    """
    ids, xs, ys = [], [], []
    for p in positions:
        ids.append(p["id"])
        xs.append(float(p["x"]))
        ys.append(float(p["y"]))
    conn.execute(
        """INSERT INTO graph_metrics (entity_id, x, y, layout_at)
           SELECT u.entity_id, u.x, u.y, now()
           FROM unnest(%s::uuid[], %s::real[], %s::real[]) AS u(entity_id, x, y)
           JOIN entity e ON e.id = u.entity_id
           ON CONFLICT (entity_id) DO UPDATE SET
             x = EXCLUDED.x, y = EXCLUDED.y, layout_at = EXCLUDED.layout_at""",
        (ids, xs, ys),
    )
    return {"saved": len(ids)}


def path_to_targets(
    conn: psycopg.Connection, from_id: str, target_ids: list[str], *,
    max_depth: int = 6,
) -> dict[str, Any]:
    """Kürzester Pfad (BFS, ungerichtet) vom Suchtreffer zu irgendeinem der
    bereits geladenen Nodes — damit Treffer nicht kontextlos schweben.

    Iterative Frontier-Expansion (eine Query pro Ebene) statt rekursiver
    Pfad-CTE: die CTE müsste Pfade enumerieren und explodiert an Hubs.
    Kein Pfad in max_depth: found=false — der Client rendert den Treffer
    als eigene Insel mit Ghost-Badges.
    """
    from_id = canonical_id(conn, from_id)
    # Nur syntaktisch gültige UUIDs — ungültige Client-IDs sollen leer matchen,
    # nicht die Query mit 22P02 abschießen.
    targets = {str(UUID(t)) for t in target_ids if _is_uuid(t)}
    if from_id in targets:
        return {"found": True, "nodes": [], "edges": []}

    parent: dict[str, str | None] = {from_id: None}
    frontier = [from_id]
    goal: str | None = None
    for _ in range(max_depth):
        if not frontier:
            break
        rows = conn.execute(
            _EDGES_SQL + """
              AND (s.subject_id = ANY(%(f)s::uuid[]) OR s.object_id = ANY(%(f)s::uuid[]))""",
            {"f": frontier},
        ).fetchall()
        nxt: list[str] = []
        for r in rows:
            a, b = str(r["subject_id"]), str(r["object_id"])
            for src, dst in ((a, b), (b, a)):
                if src in parent and dst not in parent:
                    parent[dst] = src
                    nxt.append(dst)
                    if dst in targets:
                        goal = dst
            if goal:
                break
        if goal:
            break
        frontier = nxt

    if not goal:
        return {"found": False, "nodes": [], "edges": []}

    path: list[str] = []
    cur: str | None = goal
    while cur is not None:
        path.append(cur)
        cur = parent[cur]

    nodes = conn.execute(
        """SELECT id, type_id, label,
                  coalesce(m.degree,
                    (SELECT count(*) FROM statement s
                     WHERE (s.subject_id = entity.id OR s.object_id = entity.id)
                       AND s.system_to IS NULL AND s.rank <> 'deprecated'
                       AND s.value_type = 'entity')) AS degree,
                  m.x, m.y
           FROM entity LEFT JOIN graph_metrics m ON m.entity_id = entity.id
           WHERE entity.id = ANY(%s::uuid[])""",
        (path,),
    ).fetchall()
    edges = conn.execute(
        """SELECT s.id, s.subject_id, s.object_id, s.predicate_id, s.rank, s.confidence
           FROM statement s
           WHERE s.value_type = 'entity' AND s.system_to IS NULL
             AND s.rank <> 'deprecated'
             AND s.subject_id = ANY(%(p)s::uuid[]) AND s.object_id = ANY(%(p)s::uuid[])""",
        {"p": path},
    ).fetchall()
    return {"found": True, "nodes": nodes, "edges": edges}


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False
