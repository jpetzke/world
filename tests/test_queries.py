"""Cross-Domain-Beweis (Spec §10) und Suche: Social + Finance + Geo sind
Regionen desselben Graphen — ein Multi-Hop-Traverse über EINE Struktur."""

from weltmodell.entities import create_entity
from weltmodell.queries import semantic_search, traverse
from weltmodell.statements import commit_statement


def _entity(conn, type_id, label):
    return str(create_entity(conn, type_id=type_id, label=label)["id"])


def _link(conn, subject, predicate, obj, source_id, **kwargs):
    return commit_statement(
        conn, subject_id=subject, predicate_id=predicate,
        value={"type": "entity", "object_id": obj}, source_ids=[source_id],
        **kwargs,
    )


def test_cross_domain_multi_hop(conn, source_id):
    # Person → invests_in → Company → affected_by → War → located_in → Country
    jonas = _entity(conn, "Person", "Querdomain Jonas")
    tsmc = _entity(conn, "Company", "TSMC Beispiel")
    konflikt = _entity(conn, "War", "Taiwan-Konflikt Beispiel")
    taiwan = _entity(conn, "Country", "Taiwan Beispiel")
    gegner = _entity(conn, "Country", "Gegnerland Beispiel")

    _link(conn, jonas, "invests_in", tsmc, source_id)
    _link(conn, tsmc, "affected_by", konflikt, source_id, confidence=0.8)
    _link(conn, konflikt, "located_in", taiwan, source_id)
    _link(conn, taiwan, "at_war_with", gegner, source_id,
          confidence=0.9, valid_from="2026-01-01")

    paths = traverse(conn, jonas, max_depth=4)
    by_label = {p["label"]: p for p in paths}

    assert by_label["TSMC Beispiel"]["depth"] == 1
    assert by_label["Taiwan-Konflikt Beispiel"]["depth"] == 2
    assert by_label["Taiwan Beispiel"]["depth"] == 3
    assert by_label["Gegnerland Beispiel"]["depth"] == 4
    assert by_label["Gegnerland Beispiel"]["via"] == [
        "invests_in", "affected_by", "located_in", "at_war_with",
    ]

    # Prädikat-Filter beschneidet den Walk
    only_invest = traverse(conn, jonas, max_depth=4, predicates=["invests_in"])
    assert [p["label"] for p in only_invest] == ["TSMC Beispiel"]


def test_traverse_skips_deprecated_edges(conn, source_id):
    a = _entity(conn, "Person", "Kantenperson A")
    b = _entity(conn, "Person", "Kantenperson B")
    stmt = _link(conn, a, "knows", b, source_id)

    assert [p["label"] for p in traverse(conn, a)] == ["Kantenperson B"]

    from weltmodell.statements import deprecate_statement

    deprecate_statement(conn, str(stmt["id"]))
    assert traverse(conn, a) == []


def test_semantic_search_finds_fuzzy_label(conn):
    create_entity(conn, type_id="Person", label="Suchbare Persona Unikat")
    hits = semantic_search(conn, "Suchbare Persona Unikat", type_id="Person")
    assert hits[0]["label"] == "Suchbare Persona Unikat"
    assert hits[0]["similarity"] > 0.99
