"""Entity-Resolution & Merge (Spec §7.2): deterministisch, Vektor, verlustfrei."""

import pytest

from weltmodell.entities import canonical_id, create_entity, get_entity
from weltmodell.errors import ValidationError
from weltmodell.queries import entity_view
from weltmodell.resolution import get_or_create_entity, merge_entity, resolve
from weltmodell.statements import commit_statement


def test_deterministic_match_via_identifying_key(conn, source_id):
    eid, created = get_or_create_entity(
        conn, type_id="Person", label="Dedup Kandidat Eins",
        identifiers={"email": "dedup1@example.org"}, source_ids=[source_id],
    )
    assert created
    # Zweite Quelle, anderer Name, gleiche E-Mail → derselbe Anker
    res = resolve(
        conn, type_id="Person", label="D. Kandidat",
        identifiers={"email": "dedup1@example.org"},
    )
    assert res["match"] == eid
    assert res["method"] == "deterministic:email"


def test_non_identifying_predicate_never_matches(conn, source_id):
    account = str(create_entity(
        conn, type_id="SocialMediaAccount", label="@resolvetest")["id"])
    commit_statement(
        conn, subject_id=account, predicate_id="handle",
        value={"type": "string", "text": "resolvenet"}, source_ids=[source_id],
    )
    res = resolve(conn, type_id="SocialMediaAccount",
                  identifiers={"handle": "resolvenet"})
    assert res["match"] is None


def test_vector_similarity_candidates(conn):
    create_entity(conn, type_id="Person", label="Vektoria Beispielmann")
    res = resolve(conn, type_id="Person", label="Vektoria Beispelmann")  # Tippfehler
    assert res["match"] is None
    assert res["candidates"], "Fuzzy-Kandidat erwartet"
    assert res["candidates"][0]["label"] == "Vektoria Beispielmann"
    assert res["candidates"][0]["similarity"] > 0.8


def test_get_or_create_auto_matches_identical_label(conn, source_id):
    first, created_first = get_or_create_entity(
        conn, type_id="SocialMediaAccount", label="@autodedup", source_ids=[source_id]
    )
    second, created_second = get_or_create_entity(
        conn, type_id="SocialMediaAccount", label="@autodedup", source_ids=[source_id]
    )
    assert created_first and not created_second
    assert first == second


def test_merge_preserves_statements_and_provenance(conn, source_id):
    a = str(create_entity(conn, type_id="Person", label="Merge Person A")["id"])
    b = str(create_entity(conn, type_id="Person", label="Merge Person B")["id"])
    account = str(create_entity(
        conn, type_id="SocialMediaAccount", label="@mergeacc")["id"])
    commit_statement(
        conn, subject_id=a, predicate_id="owns_account",
        value={"type": "entity", "object_id": account}, source_ids=[source_id],
    )
    commit_statement(
        conn, subject_id=b, predicate_id="email",
        value={"type": "string", "text": "merge-b@example.org"},
        source_ids=[source_id],
    )

    report = merge_entity(conn, b, a)
    assert report["into"] == a

    assert get_entity(conn, b)["merged_into"] is not None
    assert canonical_id(conn, b) == a

    view = entity_view(conn, a)
    predicates = {s["predicate_id"] for s in view["statements"]}
    assert {"owns_account", "email"} <= predicates  # nichts verloren
    email = next(s for s in view["statements"] if s["predicate_id"] == "email")
    assert email["references"], "Provenance beider Quellen bleibt"


def test_merge_rejects_type_conflict(conn):
    person = str(create_entity(conn, type_id="Person", label="Typ Konflikt P")["id"])
    account = str(create_entity(
        conn, type_id="SocialMediaAccount", label="@typkonflikt")["id"])
    with pytest.raises(ValidationError, match="Typ-Konflikt"):
        merge_entity(conn, person, account)


def test_statements_on_merged_entity_go_to_canonical(conn, source_id):
    a = str(create_entity(conn, type_id="Person", label="Kanonisch A")["id"])
    b = str(create_entity(conn, type_id="Person", label="Kanonisch B")["id"])
    merge_entity(conn, b, a)
    row = commit_statement(
        conn, subject_id=b, predicate_id="email",
        value={"type": "string", "text": "kanonisch@example.org"},
        source_ids=[source_id],
    )
    assert str(row["subject_id"]) == a
