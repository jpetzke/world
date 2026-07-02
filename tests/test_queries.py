"""Multi-Hop-Traverse und Suche: der Social-Graph ist EINE Struktur —
Person → Account → Account → Person über eine einzige Kanten-Tabelle."""

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


def test_social_multi_hop(conn, source_id):
    # Person → owns_account → Account → follows → Account → account_of → Person
    jonas = _entity(conn, "Person", "Traverse Jonas")
    acc_j = _entity(conn, "SocialMediaAccount", "@traverse_jonas")
    acc_t = _entity(conn, "SocialMediaAccount", "@traverse_tanja")
    tanja = _entity(conn, "Person", "Traverse Tanja")

    _link(conn, jonas, "owns_account", acc_j, source_id)
    _link(conn, acc_j, "follows", acc_t, source_id, confidence=0.8)
    _link(conn, acc_t, "account_of", tanja, source_id)

    paths = traverse(conn, jonas, max_depth=3)
    by_label = {p["label"]: p for p in paths}

    assert by_label["@traverse_jonas"]["depth"] == 1
    assert by_label["@traverse_tanja"]["depth"] == 2
    assert by_label["Traverse Tanja"]["depth"] == 3
    assert by_label["Traverse Tanja"]["via"] == [
        "owns_account", "follows", "account_of",
    ]

    # Prädikat-Filter beschneidet den Walk
    only_owns = traverse(conn, jonas, max_depth=3, predicates=["owns_account"])
    assert [p["label"] for p in only_owns] == ["@traverse_jonas"]


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
