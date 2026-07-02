"""Registry & Review-Gate (Spec §2, §7.1): Schema-als-Daten, kein Write am Gate vorbei."""

import pytest

from weltmodell import registry
from weltmodell.errors import RegistryError


def test_seed_upper_ontology(conn):
    person = registry.get_type(conn, "Person")
    assert person["kind"] == "continuant"
    assert registry.type_ancestors(conn, "Person") == [
        "Person", "Agent", "Continuant",
    ]
    mention = registry.get_type(conn, "Mention")
    assert mention["kind"] == "occurrent"
    assert registry.is_subtype(conn, "War", "Occurrent")


def test_seed_interfaces_inherited(conn):
    # Country erbt nichts, deklariert Nameable+Locatable; Event-Subtypen erben Temporal
    assert registry.type_interfaces(conn, "Country") == {"Nameable", "Locatable"}
    assert "Temporal" in registry.type_interfaces(conn, "War")


def test_seed_inverse_predicates(conn):
    assert registry.get_predicate(conn, "works_at")["inverse_id"] == "employs"
    assert registry.get_predicate(conn, "employs")["inverse_id"] == "works_at"
    assert registry.get_predicate(conn, "knows")["inverse_id"] == "knows"


def test_new_type_via_gate(conn):
    prop = registry.propose_type(
        conn, type_id="Earthquake", parent_id="NaturalDisaster", kind="occurrent",
        label="Earthquake", interfaces=["Locatable", "Temporal"],
        rationale="Test", proposed_by="pytest",
    )
    registry.approve_type(conn, str(prop["id"]))
    assert registry.get_type(conn, "Earthquake")["parent_id"] == "NaturalDisaster"
    assert "Locatable" in registry.type_interfaces(conn, "Earthquake")


def test_gate_rejects_continuant_occurrent_mix(conn):
    # Invariante 5: Der Continuant/Occurrent-Split ist heilig
    prop = registry.propose_type(
        conn, type_id="BrokenType", parent_id="Person", kind="occurrent",
        label="Broken", proposed_by="pytest",
    )
    with pytest.raises(RegistryError, match="heilig"):
        registry.approve_type(conn, str(prop["id"]))


def test_gate_rejects_predicate_without_domain_or_cardinality(conn):
    p1 = registry.propose_predicate(
        conn, predicate_id="floats_freely", label="x", range_kind="string",
        cardinality="1:n", proposed_by="pytest",
    )
    with pytest.raises(RegistryError, match="domain"):
        registry.approve_predicate(conn, str(p1["id"]))

    p2 = registry.propose_predicate(
        conn, predicate_id="no_cardinality", label="x", range_kind="string",
        domain_type="Person", proposed_by="pytest",
    )
    with pytest.raises(RegistryError, match="cardinality"):
        registry.approve_predicate(conn, str(p2["id"]))


def test_gate_approves_predicate_and_sets_inverse(conn):
    base = registry.propose_predicate(
        conn, predicate_id="mentors", label="mentort", range_kind="entity",
        domain_type="Person", range_type="Person", cardinality="n:m",
        proposed_by="pytest",
    )
    registry.approve_predicate(conn, str(base["id"]))
    inv = registry.propose_predicate(
        conn, predicate_id="mentored_by", label="gementort von",
        range_kind="entity", domain_type="Person", range_type="Person",
        cardinality="n:m", inverse_id="mentors", proposed_by="pytest",
    )
    registry.approve_predicate(conn, str(inv["id"]))
    assert registry.get_predicate(conn, "mentors")["inverse_id"] == "mentored_by"
    assert registry.get_predicate(conn, "mentored_by")["inverse_id"] == "mentors"


def test_gate_rejects_duplicate_type_proposal(conn):
    with pytest.raises(RegistryError, match="existiert bereits"):
        registry.propose_type(
            conn, type_id="Person", parent_id="Agent", kind="continuant",
            label="Person", proposed_by="pytest",
        )
