"""Multi-Hop-Traverse und Suche: der Social-Graph ist EINE Struktur —
Person → Account → Account → Person über eine einzige Kanten-Tabelle."""

import pytest

from weltmodell.entities import create_entity
from weltmodell.errors import ValidationError
from weltmodell.queries import neighborhood, semantic_search
from weltmodell.statements import commit_statement


def _entity(conn, type_id, label):
    return str(create_entity(conn, type_id=type_id, label=label)["id"])


def _link(conn, subject, predicate, obj, source_id, **kwargs):
    return commit_statement(
        conn, subject_id=subject, predicate_id=predicate,
        value={"type": "entity", "object_id": obj}, source_ids=[source_id],
        **kwargs,
    )


def test_neighborhood_multi_hop(conn, source_id):
    # Person → owns_account → Account → follows → Account → account_of → Person
    jonas = _entity(conn, "Person", "NB Jonas")
    acc_j = _entity(conn, "SocialMediaAccount", "@nb_jonas")
    acc_t = _entity(conn, "SocialMediaAccount", "@nb_tanja")
    tanja = _entity(conn, "Person", "NB Tanja")

    _link(conn, jonas, "owns_account", acc_j, source_id)
    _link(conn, acc_j, "follows", acc_t, source_id, confidence=0.8)
    _link(conn, acc_t, "account_of", tanja, source_id)

    g = neighborhood(conn, jonas, max_depth=3)
    depth = {n["label"]: n["depth"] for n in g["nodes"]}
    assert depth["NB Jonas"] == 0
    assert depth["@nb_jonas"] == 1
    assert depth["@nb_tanja"] == 2
    assert depth["NB Tanja"] == 3
    # Alle drei Kanten des Pfads kommen als induzierter Teilgraph zurück
    assert len(g["edges"]) == 3

    # Prädikat-Filter beschneidet den Teilgraph
    only_owns = neighborhood(conn, jonas, max_depth=3, predicates=["owns_account"])
    assert {n["label"] for n in only_owns["nodes"]} == {"NB Jonas", "@nb_jonas"}


def test_neighborhood_is_undirected(conn, source_id):
    # Der Hub hat NUR eingehende Kanten — muss seine Nachbarn trotzdem zeigen.
    hub = _entity(conn, "SocialMediaAccount", "@nb_hub")
    fan = _entity(conn, "SocialMediaAccount", "@nb_fan")
    _link(conn, fan, "follows", hub, source_id)

    g = neighborhood(conn, hub, max_depth=1)
    assert {n["label"] for n in g["nodes"]} == {"@nb_hub", "@nb_fan"}
    assert len(g["edges"]) == 1
    assert g["start_id"] == hub


def test_neighborhood_induced_cross_links(conn, source_id):
    # Dreieck A-B-C: die Diagonale A-C darf nicht fehlen (BFS-Baum verlöre sie).
    a = _entity(conn, "Person", "NB Tri A")
    b = _entity(conn, "Person", "NB Tri B")
    c = _entity(conn, "Person", "NB Tri C")
    _link(conn, a, "knows", b, source_id)
    _link(conn, b, "knows", c, source_id)
    _link(conn, a, "knows", c, source_id)

    g = neighborhood(conn, a, max_depth=2)
    assert len({n["label"] for n in g["nodes"]}) == 3
    assert len(g["edges"]) == 3


def test_neighborhood_skips_deprecated_and_isolates(conn, source_id):
    a = _entity(conn, "Person", "Kantenperson A")
    b = _entity(conn, "Person", "Kantenperson B")
    stmt = _link(conn, a, "knows", b, source_id)

    g = neighborhood(conn, a)
    assert {n["label"] for n in g["nodes"]} == {"Kantenperson A", "Kantenperson B"}

    from weltmodell.statements import deprecate_statement

    deprecate_statement(conn, str(stmt["id"]))
    g = neighborhood(conn, a)
    # Isolierter Knoten: nur er selbst, keine Kanten (nicht leer!)
    assert {n["label"] for n in g["nodes"]} == {"Kantenperson A"}
    assert g["edges"] == []


def test_neighborhood_caps_nodes_but_reports_total(conn, source_id):
    hub = _entity(conn, "SocialMediaAccount", "@nb_bighub")
    for i in range(6):
        fan = _entity(conn, "SocialMediaAccount", f"@nb_f{i}")
        _link(conn, fan, "follows", hub, source_id)

    g = neighborhood(conn, hub, max_depth=1, max_nodes=3)
    assert len(g["nodes"]) == 3          # abgeschnitten
    assert g["total_nodes"] == 7         # Hub + 6 Fans, ehrlich gezählt


def test_semantic_search_finds_fuzzy_label(conn):
    create_entity(conn, type_id="Person", label="Suchbare Persona Unikat")
    hits = semantic_search(conn, "Suchbare Persona Unikat", type_id="Person")
    assert hits[0]["label"] == "Suchbare Persona Unikat"
    assert hits[0]["similarity"] > 0.99


# --- Paket 2: welt_query — Statement-zentrierte Suche + Aggregation ----------


def test_query_statements_nach_praedikat(conn, source_id):
    from weltmodell.queries import query_statements

    e = _entity(conn, "Person", "Query Person")
    for text in ("QAlias1", "QAlias2"):
        commit_statement(
            conn, subject_id=e, predicate_id="alias",
            value={"type": "string", "text": text}, source_ids=[source_id],
        )
    res = query_statements(conn, predicate_id="alias", subject_id=e)
    assert res["total"] == 2
    assert {s["value_text"] for s in res["statements"]} == {"QAlias1", "QAlias2"}
    # Serialisierung wie entity_view: Qualifier + Quellen-Referenzen dabei
    first = res["statements"][0]
    assert "qualifiers" in first and "references" in first
    assert str(first["references"][0]["id"]) == source_id


def test_query_statements_confidence_und_rank(conn, source_id):
    from weltmodell.queries import query_statements
    from weltmodell.statements import deprecate_statement

    e1 = _entity(conn, "Person", "QConf Eins")
    e2 = _entity(conn, "Person", "QConf Zwei")
    commit_statement(
        conn, subject_id=e1, predicate_id="alias",
        value={"type": "string", "text": "QConf"}, source_ids=[source_id],
        confidence=0.4,
    )
    high = commit_statement(
        conn, subject_id=e2, predicate_id="alias",
        value={"type": "string", "text": "QConf"}, source_ids=[source_id],
        confidence=0.9,
    )
    both = query_statements(conn, predicate_id="alias", value_text="QConf")
    assert both["total"] == 2
    filtered = query_statements(
        conn, predicate_id="alias", value_text="QConf", min_confidence=0.5
    )
    assert filtered["total"] == 1
    assert filtered["statements"][0]["confidence"] == pytest.approx(0.9)

    # rank: ohne Filter ist deprecated ausgeblendet, exakter Filter findet es
    deprecate_statement(conn, str(high["id"]))
    current = query_statements(conn, predicate_id="alias", value_text="QConf")
    assert current["total"] == 1
    dep = query_statements(
        conn, predicate_id="alias", value_text="QConf", rank="deprecated"
    )
    assert dep["total"] == 1
    assert dep["statements"][0]["rank"] == "deprecated"


def test_query_statements_valid_at_zeitreise(conn, source_id):
    from weltmodell.queries import query_statements

    e = _entity(conn, "Person", "QZeit Person")
    commit_statement(
        conn, subject_id=e, predicate_id="alias",
        value={"type": "string", "text": "QZeitAlias"}, source_ids=[source_id],
        valid_from="2020-01-01T00:00:00Z", valid_to="2021-01-01T00:00:00Z",
    )
    drin = query_statements(conn, subject_id=e, valid_at="2020-06-01T00:00:00Z")
    assert drin["total"] == 1
    davor = query_statements(conn, subject_id=e, valid_at="2019-06-01T00:00:00Z")
    assert davor["total"] == 0
    danach = query_statements(conn, subject_id=e, valid_at="2022-06-01T00:00:00Z")
    assert danach["total"] == 0


def test_query_aggregation_summe_pro_unit(conn, source_id):
    from weltmodell.queries import query_statements

    fälle = [("Übernahme A", 100, "EUR"), ("Übernahme B", 50, "EUR"),
             ("Übernahme C", 10, "USD")]
    for label, betrag, unit in fälle:
        u = _entity(conn, "Übernahme", label)
        commit_statement(
            conn, subject_id=u, predicate_id="kaufpreis",
            value={"type": "quantity", "number": betrag, "unit": unit},
            source_ids=[source_id],
        )
    res = query_statements(conn, predicate_id="kaufpreis", aggregate="sum")
    by_unit = {g["unit"]: g for g in res["groups"]}
    assert by_unit["EUR"]["value"] == pytest.approx(150.0)
    assert by_unit["EUR"]["n"] == 2
    assert by_unit["USD"]["value"] == pytest.approx(10.0)

    # count ohne group_by; count mit group_by=subject liefert Entity-Gruppen
    cnt = query_statements(conn, predicate_id="kaufpreis", aggregate="count")
    assert cnt["count"] == 3
    grouped = query_statements(
        conn, predicate_id="kaufpreis", aggregate="count", group_by="subject"
    )
    assert len(grouped["groups"]) == 3
    assert all(g["count"] == 1 and g["label"] for g in grouped["groups"])


# --- Paket 3: min_confidence/rank-Filter in entity_view und neighborhood -----


def test_entity_view_read_filter(conn, source_id):
    from weltmodell.queries import entity_view
    from weltmodell.statements import deprecate_statement

    e = _entity(conn, "Person", "Filter Person")
    commit_statement(
        conn, subject_id=e, predicate_id="alias",
        value={"type": "string", "text": "FilterLow"}, source_ids=[source_id],
        confidence=0.3,
    )
    high = commit_statement(
        conn, subject_id=e, predicate_id="alias",
        value={"type": "string", "text": "FilterHigh"}, source_ids=[source_id],
        confidence=0.9,
    )
    view = entity_view(conn, e, min_confidence=0.5)
    assert [s["value_text"] for s in view["statements"]] == ["FilterHigh"]

    # rank exakt ersetzt den deprecated-Ausschluss (Semantik wie welt_query)
    deprecate_statement(conn, str(high["id"]))
    dep = entity_view(conn, e, rank="deprecated")
    assert {s["value_text"] for s in dep["statements"]} == {"FilterHigh"}


def test_neighborhood_read_filter(conn, source_id):
    a = _entity(conn, "Person", "NBF A")
    b = _entity(conn, "Person", "NBF B")
    c = _entity(conn, "Person", "NBF C")
    _link(conn, a, "knows", b, source_id, confidence=0.4)
    _link(conn, a, "knows", c, source_id, confidence=0.9)

    nb = neighborhood(conn, a, max_depth=1, min_confidence=0.5)
    ids = {str(n["id"]) for n in nb["nodes"]}
    assert c in ids and b not in ids


# --- Audit-Fixes: Validierung statt stiller Leerergebnisse -------------------


def test_query_unknown_predicate_raises(conn):
    from weltmodell.queries import query_statements

    with pytest.raises(ValidationError, match="Unbekanntes Prädikat"):
        query_statements(conn, predicate_id="nam")


def test_query_min_confidence_range_checked(conn):
    from weltmodell.queries import query_statements

    with pytest.raises(ValidationError, match="min_confidence"):
        query_statements(conn, min_confidence=80)


def test_traverse_unknown_predicate_raises(conn, source_id):
    from weltmodell.queries import neighborhood

    start = _entity(conn, "Person", "Traverse Validierung")
    with pytest.raises(ValidationError, match="Unbekannte"):
        neighborhood(conn, start, predicates=["folgt_tippfehler"])
    with pytest.raises(ValidationError, match="min_confidence"):
        neighborhood(conn, start, min_confidence=95)


def test_list_entities_subtype_and_suggestion(conn):
    from weltmodell.entities import create_entity
    from weltmodell.queries import list_entities

    create_entity(conn, type_id="Person", label="Subtyp Listen Test")
    res = list_entities(conn, type_id="Agent")
    assert any(i["label"] == "Subtyp Listen Test" for i in res["items"])
    with pytest.raises(ValidationError, match="'Person'"):
        list_entities(conn, type_id="person")


def test_get_source_reports_total(conn, source_id):
    from weltmodell.queries import get_source

    a = _entity(conn, "Person", "Source Total A")
    b = _entity(conn, "Person", "Source Total B")
    _link(conn, a, "knows", b, source_id)
    res = get_source(conn, source_id)
    assert res["statements_total"] >= len(res["statements"]) > 0
