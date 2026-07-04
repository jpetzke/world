"""Registry & Review-Gate (Spec §2, §7.1): Schema-als-Daten, kein Write am Gate vorbei."""

import pytest

from weltmodell import registry
from weltmodell.errors import RegistryError


def test_seed_types(conn):
    # Person hängt unter dem abstrakten Agent-Knoten; SocialMediaAccount ist Wurzel.
    person = registry.get_type(conn, "Person")
    assert person["kind"] == "continuant"
    assert registry.type_ancestors(conn, "Person") == ["Person", "Agent"]
    account = registry.get_type(conn, "SocialMediaAccount")
    assert account["kind"] == "continuant"
    assert registry.type_ancestors(conn, "SocialMediaAccount") == ["SocialMediaAccount"]


def test_seed_interfaces(conn):
    assert registry.type_interfaces(conn, "Person") == {"Nameable", "Embeddable"}
    assert registry.type_interfaces(conn, "SocialMediaAccount") == {"Nameable", "Embeddable"}
    # Platform bewusst ohne Embeddable (0009): statisch kuratiert, exakter Lookup
    assert "Embeddable" not in registry.type_interfaces(conn, "Platform")


def test_seed_inverse_predicates(conn):
    assert registry.get_predicate(conn, "knows")["inverse_id"] == "knows"
    assert registry.get_predicate(conn, "owns_account")["inverse_id"] == "account_of"
    assert registry.get_predicate(conn, "account_of")["inverse_id"] == "owns_account"
    assert registry.get_predicate(conn, "follows")["inverse_id"] is None


def test_new_type_via_gate(conn):
    prop = registry.propose_type(
        conn, type_id="Influencer", parent_id="Person", kind="continuant",
        label="Influencer", interfaces=["Nameable", "Embeddable"],
        rationale="Test", proposed_by="pytest",
    )
    registry.approve_type(conn, str(prop["id"]))
    assert registry.get_type(conn, "Influencer")["parent_id"] == "Person"
    assert "Nameable" in registry.type_interfaces(conn, "Influencer")


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
            conn, type_id="Person", parent_id="Person", kind="continuant",
            label="Person", proposed_by="pytest",
        )


def test_finance_seed_0011(conn):
    # Unternehmen im Agent-Ast, Übernahme im Ereignis-Ast, Wertpapier eigene Wurzel
    assert registry.type_ancestors(conn, "Unternehmen") == [
        "Unternehmen", "Organization", "Agent"]
    assert registry.get_type(conn, "Übernahme")["kind"] == "occurrent"
    assert registry.type_ancestors(conn, "Wertpapier") == ["Wertpapier"]
    # Interfaces: geerbt über Parent-Kette (Unternehmen, Übernahme), direkt (Wertpapier)
    assert registry.type_interfaces(conn, "Unternehmen") == {"Nameable", "Embeddable"}
    assert registry.type_interfaces(conn, "Übernahme") == {"Nameable", "Embeddable"}
    assert registry.type_interfaces(conn, "Wertpapier") == {"Nameable", "Embeddable"}
    # Dedup-Pfade (§14.4): harte Keys für Wertpapier und Organization-Ast
    assert registry.get_predicate(conn, "isin")["identifying"] is True
    assert registry.get_predicate(conn, "lei")["identifying"] is True
    # wikidata_qid von Ort auf Nameable angehoben (0011, Rationale 5)
    qid = registry.get_predicate(conn, "wikidata_qid")
    assert qid["domain_type"] is None
    assert qid["domain_interface"] == "Nameable"
