"""Analyse-Tools (analysis.py) gegen einen konstruierten Fixture-Graph.

Der Graph enthält gezielt alle Fälle: zwei Accounts mit teilweise
überlappenden Followern, einen 2-Hop-Pfad, zwei über einen Brückenknoten
verbundene Cliquen, eine Merge-Dublette, ein deprecated Statement, ein
abgelaufenes Valid-Time-Fenster und gespreizte Confidence.

Labels tragen das Präfix "Fx" — andere Testdateien teilen sich die
Session-DB; Assertions arbeiten deshalb immer auf den Fixture-IDs, nie
auf globalen Listen.
"""

import uuid

import pytest

from weltmodell import analysis, queries
from weltmodell.db import get_conn
from weltmodell.entities import create_entity
from weltmodell.errors import NotFoundError, ValidationError
from weltmodell.pipeline import ingest_document
from weltmodell.resolution import merge_entity
from weltmodell.statements import commit_statement, deprecate_statement

UNKNOWN_ID = str(uuid.uuid4())


def _entity(c, type_id, label):
    return str(create_entity(c, type_id=type_id, label=label)["id"])


def _link(c, s, p, o, src, **kw):
    return commit_statement(
        c, subject_id=s, predicate_id=p,
        value={"type": "entity", "object_id": o}, source_ids=[src], **kw,
    )


@pytest.fixture(scope="module")
def graph(database):
    c = get_conn()
    src = str(ingest_document(
        c, raw={"fixture": "analysis"}, url="https://example.org/fx",
        activity="test:analysis", agent="pytest",
    )["id"])

    g = {"src": src}
    # Personen + Accounts
    for key, type_id, label in [
        ("jonas", "Person", "Fx Jonas"), ("alice", "Person", "Fx Alice"),
        ("bob", "Person", "Fx Bob"), ("carla", "Person", "Fx Carla"),
        ("alice_dupe", "Person", "Fx Alice Dupe"),
        ("acc_a", "SocialMediaAccount", "@fx_alice"),
        ("acc_b", "SocialMediaAccount", "@fx_bob"),
        ("acc_c", "SocialMediaAccount", "@fx_carla"),
    ]:
        g[key] = _entity(c, type_id, label)
    for i in range(1, 6):
        g[f"f{i}"] = _entity(c, "SocialMediaAccount", f"@fx_follower{i}")
    # Zwei Cliquen + Brückenknoten
    for i in range(1, 5):
        g[f"c{i}"] = _entity(c, "Person", f"Fx C{i}")
        g[f"d{i}"] = _entity(c, "Person", f"Fx D{i}")
    g["bridge"] = _entity(c, "Person", "Fx Brücke")

    # Accounts: acc_a ← f1,f2,f3 · acc_b ← f2,f3,f4,f5 · acc_c ← f1
    _link(c, g["alice"], "owns_account", g["acc_a"], src)
    _link(c, g["bob"], "owns_account", g["acc_b"], src)
    for f in ("f1", "f2", "f3"):
        _link(c, g[f], "follows", g["acc_a"], src)
    for f in ("f2", "f3", "f4", "f5"):
        _link(c, g[f], "follows", g["acc_b"], src)
    _link(c, g["f1"], "follows", g["acc_c"], src)
    # Deprecated: f4 folgte acc_a, zurückgezogen — ohne rank-Filter unsichtbar
    dep = _link(c, g["f4"], "follows", g["acc_a"], src)
    deprecate_statement(c, str(dep["id"]))
    g["deprecated_stmt"] = str(dep["id"])

    # 2-Hop-Pfad: jonas –knows→ alice –owns_account→ acc_a
    _link(c, g["jonas"], "knows", g["alice"], src)
    # Confidence-Spreizung: jonas kennt bob nur vage
    _link(c, g["jonas"], "knows", g["bob"], src, confidence=0.4)
    # Abgelaufenes Valid-Fenster: jonas kannte carla bis 2020
    _link(c, g["jonas"], "knows", g["carla"], src, valid_to="2020-01-01T00:00:00+00:00")

    # Merge-Dublette: carla kannte die Dublette; Merge biegt aufs Original um
    _link(c, g["carla"], "knows", g["alice_dupe"], src)
    merge_entity(c, g["alice_dupe"], g["alice"])

    # Cliquen (vollvermascht) + Brücke mit je zwei Andockpunkten
    for grp in ("c", "d"):
        ids = [g[f"{grp}{i}"] for i in range(1, 5)]
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                _link(c, a, "knows", b, src)
    for anchor in ("c1", "c2", "d1", "d2"):
        _link(c, g["bridge"], "knows", g[anchor], src)

    c.commit()
    yield g
    c.close()


# --- welt_match ------------------------------------------------------------------


def test_match_folgt_a_und_b(conn, graph):
    r = analysis.match(
        conn,
        patterns=[{"s": "?f", "p": "follows", "o": graph["acc_a"]},
                  {"s": "?f", "p": "follows", "o": graph["acc_b"]}],
        select=["?f"], output="ids",
    )
    assert set(r["bindings"][i]["?f"] for i in range(len(r["bindings"]))) == \
        {graph["f2"], graph["f3"]}
    assert r["total"] == 2


def test_match_drei_pattern_kette(conn, graph):
    # Follower der Accounts von Personen, die Jonas (sicher) kennt
    r = analysis.match(
        conn,
        patterns=[
            {"s": graph["jonas"], "p": "knows", "o": "?p"},
            {"s": "?p", "p": "owns_account", "o": "?acc"},
            {"s": "?f", "p": "follows", "o": "?acc"},
        ],
        select=["?acc", "?f"], min_confidence=0.5, output="ids",
    )
    # knows→bob hat confidence 0.4 → nur alice → acc_a → f1,f2,f3
    assert {(b["?acc"], b["?f"]) for b in r["bindings"]} == {
        (graph["acc_a"], graph["f1"]),
        (graph["acc_a"], graph["f2"]),
        (graph["acc_a"], graph["f3"]),
    }


def test_match_variable_an_praedikat_position(conn, graph):
    r = analysis.match(
        conn,
        patterns=[{"s": graph["alice"], "p": "?rel", "o": graph["acc_a"]}],
        select=["?rel"],
    )
    assert [b["?rel"] for b in r["bindings"]] == ["owns_account"]


def test_match_leeres_ergebnis(conn, graph):
    r = analysis.match(
        conn,
        patterns=[{"s": "?x", "p": "follows", "o": graph["f1"]}],
        select=["?x"],
    )
    assert r["bindings"] == [] and r["total"] == 0


def test_match_pagination_total_stabil(conn, graph):
    kwargs = dict(
        patterns=[{"s": "?f", "p": "follows", "o": graph["acc_b"]}],
        select=["?f"], output="ids",
    )
    seen = []
    for offset in (0, 2, 4):
        r = analysis.match(conn, limit=2, offset=offset, **kwargs)
        assert r["total"] == 4
        seen += [b["?f"] for b in r["bindings"]]
    assert len(seen) == 4 and set(seen) == \
        {graph["f2"], graph["f3"], graph["f4"], graph["f5"]}


def test_match_merge_kette_wird_verfolgt(conn, graph):
    # Konstante zeigt auf die gemergte Dublette → löst aufs Original auf
    r = analysis.match(
        conn,
        patterns=[{"s": "?p", "p": "knows", "o": graph["alice_dupe"]}],
        select=["?p"], output="ids",
    )
    assert graph["carla"] in {b["?p"] for b in r["bindings"]}


def test_match_deprecated_unsichtbar(conn, graph):
    r = analysis.match(
        conn,
        patterns=[{"s": "?f", "p": "follows", "o": graph["acc_a"]}],
        select=["?f"], output="ids",
    )
    assert graph["f4"] not in {b["?f"] for b in r["bindings"]}
    assert r["total"] == 3


def test_match_valid_at_filtert(conn, graph):
    pattern = [{"s": graph["jonas"], "p": "knows", "o": "?p"}]
    heute = analysis.match(conn, patterns=pattern, select=["?p"],
                           valid_at="2026-01-01T00:00:00+00:00", output="ids")
    assert graph["carla"] not in {b["?p"] for b in heute["bindings"]}
    damals = analysis.match(conn, patterns=pattern, select=["?p"],
                            valid_at="2019-06-01T00:00:00+00:00", output="ids")
    assert graph["carla"] in {b["?p"] for b in damals["bindings"]}


def test_match_fehler(conn, graph):
    with pytest.raises(NotFoundError, match="nicht gefunden"):
        analysis.match(conn, patterns=[{"s": UNKNOWN_ID, "p": "knows", "o": "?x"}],
                       select=["?x"])
    with pytest.raises(ValidationError, match="welt_query"):
        analysis.match(conn, patterns=[], select=["?x"])
    with pytest.raises(ValidationError, match="in keinem Pattern"):
        analysis.match(conn, patterns=[{"s": "?a", "p": "knows", "o": "?b"}],
                       select=["?fremd"])
    with pytest.raises(ValidationError, match="Entity- UND Prädikat-Position"):
        analysis.match(
            conn, select=["?x"],
            patterns=[{"s": "?x", "p": "knows", "o": "?y"},
                      {"s": graph["jonas"], "p": "?x", "o": "?y"}],
        )
    with pytest.raises(ValidationError, match="Unbekanntes Prädikat 'erfunden'"):
        analysis.match(conn, patterns=[{"s": "?a", "p": "erfunden", "o": "?b"}],
                       select=["?a"])


# --- welt_set --------------------------------------------------------------------


def test_set_difference(conn, graph):
    r = analysis.set_operation(
        conn, operation="difference", on="subject", output="ids",
        queries=[{"predicate_id": "follows", "object_id": graph["acc_a"]},
                 {"predicate_id": "follows", "object_id": graph["acc_b"]}],
    )
    assert r["entities"] == [graph["f1"]]
    assert r["total"] == 1


def test_set_intersect_entspricht_match(conn, graph):
    r = analysis.set_operation(
        conn, operation="intersect", on="subject", output="ids",
        queries=[{"predicate_id": "follows", "object_id": graph["acc_a"]},
                 {"predicate_id": "follows", "object_id": graph["acc_b"]}],
    )
    m = analysis.match(
        conn,
        patterns=[{"s": "?f", "p": "follows", "o": graph["acc_a"]},
                  {"s": "?f", "p": "follows", "o": graph["acc_b"]}],
        select=["?f"], output="ids",
    )
    assert set(r["entities"]) == {b["?f"] for b in m["bindings"]} == \
        {graph["f2"], graph["f3"]}


def test_set_union_dedupliziert(conn, graph):
    r = analysis.set_operation(
        conn, operation="union", on="subject", output="ids",
        queries=[{"predicate_id": "follows", "object_id": graph["acc_a"]},
                 {"predicate_id": "follows", "object_id": graph["acc_b"]}],
    )
    # f2/f3 folgen beiden — dedupliziert bleiben genau f1..f5
    assert r["total"] == 5
    assert set(r["entities"]) == {graph[f"f{i}"] for i in range(1, 6)}


def test_set_fehler_und_leer(conn, graph):
    with pytest.raises(ValidationError, match="2 bis 10"):
        analysis.set_operation(conn, operation="union", on="subject",
                               queries=[{"predicate_id": "follows"}])
    with pytest.raises(ValidationError, match="unbekannte Filter"):
        analysis.set_operation(
            conn, operation="union", on="subject",
            queries=[{"predicate_id": "follows"}, {"rank": "preferred"}],
        )
    with pytest.raises(ValidationError, match="Ungültige operation"):
        analysis.set_operation(conn, operation="xor", on="subject",
                               queries=[{}, {}])
    leer = analysis.set_operation(
        conn, operation="difference", on="subject", output="ids",
        queries=[{"predicate_id": "follows", "object_id": graph["acc_a"]},
                 {"predicate_id": "follows", "object_id": graph["acc_a"]}],
    )
    assert leer["entities"] == [] and leer["total"] == 0


# --- welt_path -------------------------------------------------------------------


def test_path_zwei_hops(conn, graph):
    r = analysis.paths(conn, start_id=graph["jonas"], end_id=graph["acc_a"],
                       output="ids")
    assert r["path_length"] == 2 and r["total"] == 1
    assert r["paths"][0]["nodes"] == [graph["jonas"], graph["alice"], graph["acc_a"]]
    edges = r["paths"][0]["edges"]
    assert [e["predicate"] for e in edges] == ["knows", "owns_account"]
    assert edges[0]["direction"] == "out"


def test_path_kein_pfad(conn, graph):
    einsam = _entity(conn, "Person", "Fx Einsam")
    r = analysis.paths(conn, start_id=graph["jonas"], end_id=einsam)
    assert r["paths"] == [] and r["total"] == 0
    assert "Kein Pfad" in r["message"]


def test_path_predicates_filter_schliesst_pfad_aus(conn, graph):
    mit = analysis.paths(conn, start_id=graph["jonas"], end_id=graph["acc_a"])
    assert mit["paths"]
    ohne = analysis.paths(conn, start_id=graph["jonas"], end_id=graph["acc_a"],
                          predicates=["follows"])
    assert ohne["paths"] == []


def test_path_zyklus_terminiert(conn, graph):
    # Cliquen sind voller Zyklen; Pfad durch die Brücke terminiert und ist kürzest
    r = analysis.paths(conn, start_id=graph["c3"], end_id=graph["d3"],
                       predicates=["knows"], output="ids")
    # c3 → {c1|c2} → bridge → {d1|d2} → d3 = 4 Hops, 4 kürzeste Varianten
    assert r["path_length"] == 4
    assert r["total"] == 4
    for p in r["paths"]:
        assert len(p["nodes"]) == len(set(p["nodes"]))  # zyklensicher
        assert graph["bridge"] in p["nodes"]


def test_path_min_confidence(conn, graph):
    direkt = analysis.paths(conn, start_id=graph["jonas"], end_id=graph["bob"],
                            predicates=["knows"])
    assert direkt["path_length"] == 1
    gefiltert = analysis.paths(conn, start_id=graph["jonas"], end_id=graph["bob"],
                               predicates=["knows"], min_confidence=0.5)
    assert gefiltert["paths"] == []


def test_path_merge_kette(conn, graph):
    r = analysis.paths(conn, start_id=graph["alice_dupe"], end_id=graph["jonas"],
                       output="ids")
    assert r["path_length"] == 1
    assert r["paths"][0]["nodes"][0] == graph["alice"]


def test_path_fehler(conn, graph):
    with pytest.raises(NotFoundError, match="nicht gefunden"):
        analysis.paths(conn, start_id=UNKNOWN_ID, end_id=graph["jonas"])
    with pytest.raises(ValidationError, match="max_depth"):
        analysis.paths(conn, start_id=graph["jonas"], end_id=graph["alice"],
                       max_depth=0)
    with pytest.raises(ValidationError, match="keine gültige Entity-ID"):
        analysis.paths(conn, start_id="keine-uuid", end_id=graph["jonas"])


# --- welt_common -----------------------------------------------------------------


def test_common_gemeinsame_follower(conn, graph):
    r = analysis.common_neighbors(
        conn, entity_ids=[graph["acc_a"], graph["acc_b"]],
        predicates=["follows"], direction="in", output="ids",
    )
    assert {n["entity"] for n in r["neighbors"]} == {graph["f2"], graph["f3"]}
    for n in r["neighbors"]:
        assert n["shared_count"] == 2
        assert set(n["shared_with"]) == {graph["acc_a"], graph["acc_b"]}


def test_common_min_shared_bei_drei_eingaben(conn, graph):
    r = analysis.common_neighbors(
        conn, entity_ids=[graph["acc_a"], graph["acc_b"], graph["acc_c"]],
        predicates=["follows"], direction="in", min_shared=2, output="ids",
    )
    by_id = {n["entity"]: n for n in r["neighbors"]}
    # f1 folgt acc_a UND acc_c (Teil-Überlappung), f2/f3 folgen a+b
    assert set(by_id) == {graph["f1"], graph["f2"], graph["f3"]}
    assert set(by_id[graph["f1"]]["shared_with"]) == {graph["acc_a"], graph["acc_c"]}


def test_common_direction_unterscheidet(conn, graph):
    out = analysis.common_neighbors(
        conn, entity_ids=[graph["f2"], graph["f3"]],
        predicates=["follows"], direction="out", output="ids",
    )
    assert {n["entity"] for n in out["neighbors"]} == \
        {graph["acc_a"], graph["acc_b"]}
    rein = analysis.common_neighbors(
        conn, entity_ids=[graph["f2"], graph["f3"]],
        predicates=["follows"], direction="in", output="ids",
    )
    assert rein["neighbors"] == []


def test_common_fehler(conn, graph):
    with pytest.raises(ValidationError, match="2 bis 10"):
        analysis.common_neighbors(conn, entity_ids=[graph["acc_a"]])
    with pytest.raises(NotFoundError, match="nicht gefunden"):
        analysis.common_neighbors(conn, entity_ids=[graph["acc_a"], UNKNOWN_ID])
    with pytest.raises(ValidationError, match="Duplikate"):
        analysis.common_neighbors(
            conn, entity_ids=[graph["alice"], graph["alice_dupe"]],
        )
    with pytest.raises(ValidationError, match="Ungültige direction"):
        analysis.common_neighbors(
            conn, entity_ids=[graph["acc_a"], graph["acc_b"]], direction="up",
        )


# --- welt_rank -------------------------------------------------------------------


def test_rank_degree_stimmt_mit_kanten(conn, graph):
    r = analysis.rank_entities(conn, metric="degree", predicates=["follows"],
                               top=200)
    scores = {i["id"]: i["score"] for i in r["items"]}
    assert scores[graph["acc_a"]] == 3  # f1,f2,f3 — deprecated f4 zählt nicht
    assert scores[graph["acc_b"]] == 4
    assert scores[graph["acc_c"]] == 1


def test_rank_betweenness_findet_bruecke(conn, graph):
    r = analysis.rank_entities(conn, metric="betweenness",
                               predicates=["knows"], top=200)
    scores = {i["id"]: i["score"] for i in r["items"]}
    fixture_nodes = [graph[f"{grp}{i}"] for grp in ("c", "d") for i in range(1, 5)]
    assert all(scores[graph["bridge"]] > scores[n] for n in fixture_nodes)


def test_rank_type_id_filter(conn, graph):
    r = analysis.rank_entities(conn, metric="degree", predicates=["follows"],
                               type_id="SocialMediaAccount", top=200)
    assert all(i["type_id"] == "SocialMediaAccount" for i in r["items"])
    # Agent (abstrakter Obertyp) findet Personen subtypfähig
    r = analysis.rank_entities(conn, metric="degree", predicates=["knows"],
                               type_id="Agent", top=200)
    assert any(i["id"] == graph["bridge"] for i in r["items"])


def test_rank_fehler_und_leer(conn, graph):
    with pytest.raises(ValidationError, match="Ungültige metric"):
        analysis.rank_entities(conn, metric="closeness")
    with pytest.raises(ValidationError, match="Unbekannter Typ"):
        analysis.rank_entities(conn, metric="degree", type_id="GibtEsNicht")
    leer = analysis.rank_entities(conn, metric="pagerank",
                                  predicates=["account_of"])
    assert leer["items"] == [] and leer["total"] == 0


# --- welt_cluster ----------------------------------------------------------------


def test_cluster_trennt_freundeskreise(conn, graph):
    r = analysis.cluster(conn, predicates=["knows"], algorithm="louvain")
    cluster_von = {
        m["id"]: idx
        for idx, cl in enumerate(r["clusters"]) for m in cl["members"]
    }
    c_ids = [graph[f"c{i}"] for i in range(1, 5)]
    d_ids = [graph[f"d{i}"] for i in range(1, 5)]
    assert len({cluster_von[i] for i in c_ids}) == 1
    assert len({cluster_von[i] for i in d_ids}) == 1
    assert cluster_von[c_ids[0]] != cluster_von[d_ids[0]]


def test_cluster_min_size_filtert(conn, graph):
    r = analysis.cluster(conn, predicates=["knows"], min_size=10)
    fixture_ids = {graph[f"c{i}"] for i in range(1, 5)}
    members = {m["id"] for cl in r["clusters"] for m in cl["members"]}
    assert not members & fixture_ids
    # total zählt weiter alle Cluster vor dem Filter
    assert r["total"] >= 2


def test_cluster_fehler(conn, graph):
    with pytest.raises(ValidationError, match="Ungültiger algorithm"):
        analysis.cluster(conn, algorithm="kmeans")
    with pytest.raises(ValidationError, match="min_size"):
        analysis.cluster(conn, min_size=0)


# --- welt_similar ----------------------------------------------------------------


def test_similar_rankt_nach_ueberlappung(conn, graph):
    r = analysis.similar(conn, entity_id=graph["acc_a"],
                         predicates=["follows"], direction="in")
    by_id = {i["entity"]["id"]: i for i in r["items"]}
    # acc_b: 2 von 5 gemeinsam (0.4) > acc_c: 1 von 3 (0.33)
    assert by_id[graph["acc_b"]]["overlap"] == 2
    assert by_id[graph["acc_c"]]["overlap"] == 1
    assert by_id[graph["acc_b"]]["score"] > by_id[graph["acc_c"]]["score"]
    ids = [i["entity"]["id"] for i in r["items"]]
    assert ids.index(graph["acc_b"]) < ids.index(graph["acc_c"])


def test_similar_ohne_nachbarn_leer(conn, graph):
    einsam = _entity(conn, "Person", "Fx Similar Einsam")
    r = analysis.similar(conn, entity_id=einsam)
    assert r["items"] == [] and r["total"] == 0


def test_similar_fehler(conn, graph):
    with pytest.raises(NotFoundError, match="nicht gefunden"):
        analysis.similar(conn, entity_id=UNKNOWN_ID)
    with pytest.raises(ValidationError, match="Ungültige direction"):
        analysis.similar(conn, entity_id=graph["acc_a"], direction="sideways")


# --- welt_changes ----------------------------------------------------------------


def test_changes_added_und_deprecated(conn, graph):
    t0 = conn.execute("SELECT now() AS t").fetchone()["t"].isoformat()
    stmt = _link(conn, graph["f5"], "follows", graph["acc_c"], graph["src"])
    sid = str(stmt["id"])

    added = analysis.changes(conn, since=t0, kind="added", output="ids")
    assert sid in added["changes"]

    deprecate_statement(conn, sid)
    dep = analysis.changes(conn, since=t0, kind="deprecated")
    dep_rows = [r for r in dep["changes"] if str(r["subject_id"]) == graph["f5"]]
    assert dep_rows and all(r["change"] == "deprecated" for r in dep_rows)
    assert all(r["changed_at"] is not None for r in dep_rows)


def test_changes_leeres_fenster(conn, graph):
    r = analysis.changes(conn, since="2000-01-01T00:00:00+00:00",
                         until="2001-01-01T00:00:00+00:00")
    assert r["changes"] == [] and r["total"] == 0


def test_changes_fehler(conn, graph):
    with pytest.raises(ValidationError, match="Fenster ist leer"):
        analysis.changes(conn, since="2026-01-01T00:00:00+00:00",
                         until="2020-01-01T00:00:00+00:00")
    with pytest.raises(ValidationError, match="ISO-Datetime"):
        analysis.changes(conn, since="gestern")
    with pytest.raises(ValidationError, match="Ungültiges kind"):
        analysis.changes(conn, since="2020-01-01T00:00:00+00:00", kind="removed")


# --- welt_sql --------------------------------------------------------------------


def test_sql_select_ueber_views(conn, graph):
    conn.commit()  # read-only Transaktion braucht einen frischen Tx-Anfang
    r = analysis.sql_query(
        conn,
        query="SELECT subject_id FROM v_statements "
              "WHERE predicate_id = 'follows' AND system_to IS NULL "
              "AND rank <> 'deprecated' AND object_id = "
              f"'{graph['acc_a']}'",
    )
    assert {str(row["subject_id"]) for row in r["rows"]} == \
        {graph["f1"], graph["f2"], graph["f3"]}
    assert r["truncated"] is False


def test_sql_row_cap(conn, graph):
    conn.commit()
    r = analysis.sql_query(conn, query="SELECT id FROM v_entities", limit=2)
    assert r["row_count"] == 2 and r["truncated"] is True


def test_sql_ablehnungen(conn, graph):
    cases = [
        ("UPDATE v_statements SET rank = 'preferred'", "read-only"),
        ("DELETE FROM v_statements", "read-only"),
        ("SELECT * FROM statement", "nicht erlaubt"),
        ("SELECT 1; SELECT 2", "Genau ein Statement"),
        ("SELECT pg_sleep(10)", "nicht erlaubt"),
    ]
    for query, text in cases:
        with pytest.raises(ValidationError, match=text):
            analysis.sql_query(conn, query=query)


# --- Shared-Semantik über alle neuen Tools ----------------------------------------


def test_output_serialisierung_einheitlich(conn, graph):
    """Dieselbe Entity wird von match, set und common identisch serialisiert."""
    for output in ("ids", "compact", "full"):
        m = analysis.match(
            conn, patterns=[{"s": "?f", "p": "follows", "o": graph["acc_a"]},
                            {"s": "?f", "p": "follows", "o": graph["acc_b"]}],
            select=["?f"], output=output,
        )
        s = analysis.set_operation(
            conn, operation="intersect", on="subject", output=output,
            queries=[{"predicate_id": "follows", "object_id": graph["acc_a"]},
                     {"predicate_id": "follows", "object_id": graph["acc_b"]}],
        )
        c = analysis.common_neighbors(
            conn, entity_ids=[graph["acc_a"], graph["acc_b"]],
            predicates=["follows"], direction="in", output=output,
        )

        def key(e):
            return e if isinstance(e, str) else str(e["id"])

        froms = {
            "match": {key(b["?f"]): b["?f"] for b in m["bindings"]},
            "set": {key(e): e for e in s["entities"]},
            "common": {key(n["entity"]): n["entity"] for n in c["neighbors"]},
        }
        assert set(froms["match"]) == set(froms["set"]) == set(froms["common"])
        for eid in froms["match"]:
            assert froms["match"][eid] == froms["set"][eid] == froms["common"][eid]
        if output == "compact":
            sample = next(iter(froms["set"].values()))
            assert set(sample.keys()) == {"id", "label", "type_id"}


def test_ids_output_erlaubt_hohes_limit(conn, graph):
    r = analysis.set_operation(
        conn, operation="union", on="subject", limit=5000, output="ids",
        queries=[{"predicate_id": "follows"}, {"predicate_id": "knows"}],
    )
    assert r["limit"] == 5000
    r = analysis.set_operation(
        conn, operation="union", on="subject", limit=5000, output="compact",
        queries=[{"predicate_id": "follows"}, {"predicate_id": "knows"}],
    )
    assert r["limit"] == 500


def test_full_output_wie_welt_entity(conn, graph):
    """full-Statements tragen Qualifier + Quellen — exakt die Felder der
    entity_view-Serialisierung."""
    t0 = "2000-01-01T00:00:00+00:00"
    r = analysis.changes(conn, since=t0, subject_id=graph["f1"],
                         predicate_id="follows", output="full")
    assert r["changes"]
    ref = queries.entity_view(conn, graph["f1"])["statements"]
    ref_follows = [s for s in ref if s["predicate_id"] == "follows"]
    assert ref_follows
    full_keys = set(ref_follows[0].keys())
    for row in r["changes"]:
        row.pop("change"), row.pop("changed_at")
        assert set(row.keys()) <= full_keys | {"object_label", "object_type",
                                               "subject_label", "subject_type"}
        assert "qualifiers" in row and "references" in row
    kompakt = analysis.changes(conn, since=t0, subject_id=graph["f1"],
                               predicate_id="follows", output="compact")
    for row in kompakt["changes"]:
        assert "qualifiers" not in row and "references" not in row


# --- Retrofit: output an welt_query / welt_traverse --------------------------------


def test_query_output_retrofit(conn, graph):
    full = queries.query_statements(conn, predicate_id="follows",
                                    object_id=graph["acc_a"])
    assert all("qualifiers" in s for s in full["statements"])
    compact = queries.query_statements(conn, predicate_id="follows",
                                       object_id=graph["acc_a"], output="compact")
    assert all("qualifiers" not in s for s in compact["statements"])
    ids = queries.query_statements(conn, predicate_id="follows",
                                   object_id=graph["acc_a"], output="ids",
                                   limit=5000)
    assert ids["total"] == full["total"] == 3
    assert set(ids["ids"]) == {str(s["id"]) for s in full["statements"]}
    with pytest.raises(ValidationError, match="Ungültiges output"):
        queries.query_statements(conn, output="alles")


def test_traverse_output_retrofit(conn, graph):
    full = queries.neighborhood(conn, graph["jonas"], max_depth=1)
    assert all("depth" in n for n in full["nodes"])
    compact = queries.neighborhood(conn, graph["jonas"], max_depth=1,
                                   output="compact")
    assert all(set(n.keys()) == {"id", "label", "type_id"}
               for n in compact["nodes"])
    assert "edges" in compact
    ids = queries.neighborhood(conn, graph["jonas"], max_depth=1, output="ids")
    assert set(ids["nodes"]) == {str(n["id"]) for n in full["nodes"]}
    assert "edges" not in ids
    assert ids["total_nodes"] == full["total_nodes"]
