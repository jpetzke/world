"""Multi-Hop-Traverse und Suche: der Social-Graph ist EINE Struktur —
Person → Account → Account → Person über eine einzige Kanten-Tabelle."""

from weltmodell.entities import create_entity
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
