"""Skeleton, Layout-Persistenz und Pfad-zum-Skeleton: das DoI-Rendering-Backend.

Metriken (Leiden/PageRank/Grad) sind ableitbarer Cache (Invariante 1) —
Recompute muss idempotent sein und Client-Positionen überleben lassen.
"""

from weltmodell import graph_metrics
from weltmodell.entities import create_entity
from weltmodell.statements import commit_statement


def _entity(conn, type_id, label):
    return str(create_entity(conn, type_id=type_id, label=label)["id"])


def _link(conn, subject, predicate, obj, source_id, **kwargs):
    return commit_statement(
        conn, subject_id=subject, predicate_id=predicate,
        value={"type": "entity", "object_id": obj}, source_ids=[source_id],
        **kwargs,
    )


def _two_cliques(conn, source_id, tag):
    """Zwei dichte 4er-Cliquen, verbunden über eine Brücke — Leiden muss sie
    als getrennte Communities sehen, das Skeleton beide vertreten."""
    left = [_entity(conn, "Person", f"{tag} L{i}") for i in range(4)]
    right = [_entity(conn, "Person", f"{tag} R{i}") for i in range(4)]
    for group in (left, right):
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                _link(conn, group[i], "knows", group[j], source_id)
    _link(conn, left[0], "knows", right[0], source_id)
    return left, right


def test_recompute_covers_all_and_separates_communities(conn, source_id):
    left, right = _two_cliques(conn, source_id, "GM1")
    stats = graph_metrics.recompute(conn)
    assert stats["entities"] >= 8
    assert stats["communities"] >= 2

    rows = conn.execute(
        "SELECT entity_id, community, pagerank, degree FROM graph_metrics"
        " WHERE entity_id = ANY(%s::uuid[])", (left + right,)
    ).fetchall()
    assert len(rows) == 8
    by_id = {str(r["entity_id"]): r for r in rows}
    # Cliquen landen in unterschiedlichen Communities
    assert by_id[left[1]]["community"] != by_id[right[1]]["community"]
    # Grad zählt Statements: Innere haben 3, die Brücken-Nodes 4
    assert by_id[left[1]]["degree"] == 3
    assert by_id[left[0]]["degree"] == 4
    assert all(r["pagerank"] > 0 for r in rows)


def test_recompute_preserves_client_positions(conn, source_id):
    a = _entity(conn, "Person", "GM Pos A")
    b = _entity(conn, "Person", "GM Pos B")
    _link(conn, a, "knows", b, source_id)
    graph_metrics.recompute(conn)
    graph_metrics.save_positions(conn, [{"id": a, "x": 13.5, "y": -7.25}])

    graph_metrics.recompute(conn)  # darf x/y nicht plattmachen

    row = conn.execute(
        "SELECT x, y, metrics_at, layout_at FROM graph_metrics WHERE entity_id = %s",
        (a,),
    ).fetchone()
    assert row["x"] == 13.5 and row["y"] == -7.25
    assert row["metrics_at"] is not None and row["layout_at"] is not None


def test_skeleton_represents_every_community(conn, source_id):
    left, right = _two_cliques(conn, source_id, "GM2")
    graph_metrics.recompute(conn)

    result = graph_metrics.skeleton(conn, budget=3000)
    ids = {str(n["id"]) for n in result["nodes"]}
    # Beide Regionen vertreten — nicht nur das dichteste Ego-Netz
    assert ids & set(left) and ids & set(right)
    assert result["total_nodes"] >= 8
    # Kanten nur zwischen gelieferten Nodes
    for e in result["edges"]:
        assert str(e["subject_id"]) in ids and str(e["object_id"]) in ids
    # Node-DTO trägt, was der Client fürs DoI-Rendering braucht
    node = result["nodes"][0]
    for key in ("id", "type_id", "label", "degree", "community", "pagerank", "x", "y"):
        assert key in node


def test_skeleton_budget_caps_selection(conn, source_id):
    _two_cliques(conn, source_id, "GM3")
    graph_metrics.recompute(conn)
    result = graph_metrics.skeleton(conn, budget=50)
    assert len(result["nodes"]) <= 50


def test_skeleton_computes_lazily_on_empty_cache(conn, source_id):
    a = _entity(conn, "Person", "GM Lazy A")
    b = _entity(conn, "Person", "GM Lazy B")
    _link(conn, a, "knows", b, source_id)
    conn.execute("DELETE FROM graph_metrics")

    result = graph_metrics.skeleton(conn)
    assert result["metrics_at"] is not None
    assert {str(n["id"]) for n in result["nodes"]} >= {a, b}


def test_path_to_targets_finds_shortest(conn, source_id):
    # Kette a—b—c—d: Pfad von d zum "Skeleton" {a} muss alle vier liefern
    a = _entity(conn, "Person", "GM Path A")
    b = _entity(conn, "Person", "GM Path B")
    c = _entity(conn, "Person", "GM Path C")
    d = _entity(conn, "Person", "GM Path D")
    _link(conn, a, "knows", b, source_id)
    _link(conn, b, "knows", c, source_id)
    _link(conn, c, "knows", d, source_id)

    result = graph_metrics.path_to_targets(conn, d, [a])
    assert result["found"]
    assert {str(n["id"]) for n in result["nodes"]} == {a, b, c, d}
    assert len(result["edges"]) == 3


def test_path_to_targets_island_reports_not_found(conn, source_id):
    island = _entity(conn, "Person", "GM Insel")
    target = _entity(conn, "Person", "GM Festland")
    result = graph_metrics.path_to_targets(conn, island, [target])
    assert result == {"found": False, "nodes": [], "edges": []}


def test_path_ignores_invalid_target_ids(conn, source_id):
    a = _entity(conn, "Person", "GM Inv A")
    b = _entity(conn, "Person", "GM Inv B")
    _link(conn, a, "knows", b, source_id)
    result = graph_metrics.path_to_targets(conn, a, ["nicht-uuid", b])
    assert result["found"]


def test_api_endpoints(client):
    # Skeleton (read)
    r = client.get("/api/graph/skeleton")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"nodes", "edges", "total_nodes", "metrics_at"} <= set(body)

    if body["nodes"]:
        nid = body["nodes"][0]["id"]
        # Positionen persistieren (write)
        r = client.post("/api/graph/positions",
                        json={"positions": [{"id": nid, "x": 1.0, "y": 2.0}]})
        assert r.status_code == 200, r.text
        assert r.json() == {"saved": 1}
        # Pfad (read)
        r = client.post("/api/graph/path",
                        json={"from_id": nid, "target_ids": [nid]})
        assert r.status_code == 200, r.text
        assert r.json()["found"] is True

    # Recompute (admin)
    r = client.post("/api/graph/metrics/recompute")
    assert r.status_code == 200, r.text
    assert "entities" in r.json()
