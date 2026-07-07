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


# --- Paket 1 (0013/0014): Root-Typen, identifying, label_predicate, abstract ---


def test_root_type_via_gate(conn):
    prop = registry.propose_type(
        conn, type_id="TestWurzel", kind="continuant", label="Test-Wurzel",
        rationale="Root-Typ-Test", proposed_by="pytest",
    )
    assert prop["parent_id"] is None
    registry.approve_type(conn, str(prop["id"]))
    assert registry.get_type(conn, "TestWurzel")["parent_id"] is None
    assert registry.type_ancestors(conn, "TestWurzel") == ["TestWurzel"]
    assert any(t["id"] == "TestWurzel" for t in registry.vocabulary(conn)["types"])


def test_root_fehlwege_bleiben_fehler(conn):
    # 'Continuant' ist bewusst kein Typ (kind ist das Etikett)
    p1 = registry.propose_type(
        conn, type_id="Kaputt1", parent_id="Continuant", kind="continuant",
        label="x", proposed_by="pytest",
    )
    with pytest.raises(RegistryError, match="existiert nicht"):
        registry.approve_type(conn, str(p1["id"]))
    # Leerstring ist KEIN Root-Signal — nur echtes null
    p2 = registry.propose_type(
        conn, type_id="Kaputt2", parent_id="", kind="continuant",
        label="x", proposed_by="pytest",
    )
    with pytest.raises(RegistryError, match="existiert nicht"):
        registry.approve_type(conn, str(p2["id"]))
    # Selbstreferenz
    p3 = registry.propose_type(
        conn, type_id="Kaputt3", parent_id="Kaputt3", kind="continuant",
        label="x", proposed_by="pytest",
    )
    with pytest.raises(RegistryError, match="existiert nicht"):
        registry.approve_type(conn, str(p3["id"]))


def test_identifying_proposebar_roundtrip(conn, source_id):
    from weltmodell.entities import create_entity
    from weltmodell.resolution import resolve
    from weltmodell.statements import commit_statement

    prop = registry.propose_predicate(
        conn, predicate_id="test_kennung", label="Test-Kennung",
        range_kind="string", domain_type="Person", cardinality="1:1",
        identifying=True, proposed_by="pytest",
    )
    registry.approve_predicate(conn, str(prop["id"]))
    assert registry.get_predicate(conn, "test_kennung")["identifying"] is True

    person = create_entity(conn, type_id="Person", label="Kennung Person")
    commit_statement(
        conn, subject_id=str(person["id"]), predicate_id="test_kennung",
        value={"type": "string", "text": "KEY-123"}, source_ids=[source_id],
    )
    res = resolve(conn, type_id="Person", identifiers={"test_kennung": "KEY-123"})
    assert res["match"] == str(person["id"])
    assert res["method"] == "deterministic:test_kennung"


def test_identifying_erfordert_string_1_1(conn):
    # Fail fast: der Shape-Verstoß wird schon beim Propose abgelehnt …
    with pytest.raises(RegistryError, match="identifying"):
        registry.propose_predicate(
            conn, predicate_id="kaputt_ident_card", label="x",
            range_kind="string", domain_type="Person", cardinality="1:n",
            identifying=True, proposed_by="pytest",
        )
    # … und der Approve prüft dieselbe Regel erneut (ein Amend kann den
    # Shape nachträglich kaputt machen).
    p = registry.propose_predicate(
        conn, predicate_id="kaputt_ident_amend", label="x", range_kind="string",
        domain_type="Person", cardinality="1:1", identifying=True,
        proposed_by="pytest",
    )
    registry.amend_proposal(conn, str(p["id"]), {"cardinality": "1:n"})
    with pytest.raises(RegistryError, match="identifying"):
        registry.approve_predicate(conn, str(p["id"]))


def test_identifying_index_blockt_dublette(conn, source_id):
    from weltmodell.entities import create_entity
    from weltmodell.errors import ValidationError
    from weltmodell.statements import commit_statement

    prop = registry.propose_predicate(
        conn, predicate_id="test_kennung_uniq", label="x", range_kind="string",
        domain_type="Person", cardinality="1:1", identifying=True,
        proposed_by="pytest",
    )
    registry.approve_predicate(conn, str(prop["id"]))

    e1 = create_entity(conn, type_id="Person", label="Uniq Eins")
    e2 = create_entity(conn, type_id="Person", label="Uniq Zwei")
    commit_statement(
        conn, subject_id=str(e1["id"]), predicate_id="test_kennung_uniq",
        value={"type": "string", "text": "DUP-1"}, source_ids=[source_id],
    )
    # Klare Meldung mit Kurations-Hinweis statt roher UniqueViolation
    # (der partielle Unique-Index bleibt als DB-seitiges Sicherheitsnetz).
    with pytest.raises(ValidationError, match="welt_merge_entities"):
        commit_statement(
            conn, subject_id=str(e2["id"]), predicate_id="test_kennung_uniq",
            value={"type": "string", "text": "DUP-1"}, source_ids=[source_id],
        )


def test_ensure_identifying_index_berichtet_konflikte(conn, source_id):
    from weltmodell.entities import create_entity
    from weltmodell.statements import commit_statement

    e1 = create_entity(conn, type_id="Person", label="Konflikt Eins")
    e2 = create_entity(conn, type_id="Person", label="Konflikt Zwei")
    for e in (e1, e2):
        commit_statement(
            conn, subject_id=str(e["id"]), predicate_id="alias",
            value={"type": "string", "text": "Doppelalias"}, source_ids=[source_id],
        )
    # Gleiche Semantik wie Migration 0014: berichten, nicht bereinigen
    with pytest.raises(RegistryError, match="Dubletten"):
        registry.ensure_identifying_index(conn, "alias")
    conn.rollback()


def test_label_predicate_und_abstract_via_gate(conn, source_id):
    from weltmodell.entities import create_entity
    from weltmodell.errors import ValidationError
    from weltmodell.statements import commit_statement

    root = registry.propose_type(
        conn, type_id="TestAst", kind="continuant", label="Test-Ast",
        abstract=True, interfaces=["Nameable"], proposed_by="pytest",
    )
    registry.approve_type(conn, str(root["id"]))
    child = registry.propose_type(
        conn, type_id="TestBlatt", parent_id="TestAst", kind="continuant",
        label="Test-Blatt", label_predicate="name", proposed_by="pytest",
    )
    registry.approve_type(conn, str(child["id"]))
    assert registry.get_type(conn, "TestBlatt")["label_predicate"] == "name"

    # abstract: nicht instanziierbar, Fehlertext nennt die konkreten Kindtypen
    with pytest.raises(ValidationError, match="TestBlatt"):
        create_entity(conn, type_id="TestAst")

    # label_predicate wirkt: name-Statement aktualisiert den Label-Cache
    e = create_entity(conn, type_id="TestBlatt", label="alt")
    commit_statement(
        conn, subject_id=str(e["id"]), predicate_id="name",
        value={"type": "string", "text": "Neuer Name"}, source_ids=[source_id],
    )
    label = conn.execute(
        "SELECT label FROM entity WHERE id = %s", (e["id"],)
    ).fetchone()["label"]
    assert label == "Neuer Name"


def test_label_predicate_domain_inkompatibel(conn):
    p = registry.propose_type(
        conn, type_id="KaputtLabel", parent_id="Person", kind="continuant",
        label="x", label_predicate="handle", proposed_by="pytest",
    )
    with pytest.raises(RegistryError, match="domain-kompatibel"):
        registry.approve_type(conn, str(p["id"]))


# --- Paket 3: Interface-Proposals, Amend, Bulk-Propose ------------------------


def test_interface_proposal_roundtrip(conn):
    prop = registry.propose_interface(
        conn, interface_id="TestFähig", label="Test-Fähigkeit",
        rationale="Interface-Gate-Test", proposed_by="pytest",
    )
    registry.approve_interface(conn, str(prop["id"]))
    assert any(i["id"] == "TestFähig" for i in registry.list_interfaces(conn))

    # danach in propose_type als Interface referenzierbar …
    t = registry.propose_type(
        conn, type_id="TestFähigTyp", parent_id="Person", kind="continuant",
        label="x", interfaces=["TestFähig"], proposed_by="pytest",
    )
    registry.approve_type(conn, str(t["id"]))
    assert "TestFähig" in registry.type_interfaces(conn, "TestFähigTyp")

    # … und in propose_predicate als domain_interface
    p = registry.propose_predicate(
        conn, predicate_id="test_faehig_pred", label="x", range_kind="string",
        domain_interface="TestFähig", cardinality="1:n", proposed_by="pytest",
    )
    registry.approve_predicate(conn, str(p["id"]))
    assert registry.get_predicate(
        conn, "test_faehig_pred")["domain_interface"] == "TestFähig"

    assert "interfaces" in registry.list_proposals(conn, status="approved")


def test_interface_reject_und_dublette(conn):
    # Nameable existiert bereits → propose lehnt sofort ab
    with pytest.raises(RegistryError, match="existiert bereits"):
        registry.propose_interface(
            conn, interface_id="Nameable", label="dupe", proposed_by="pytest",
        )
    p = registry.propose_interface(
        conn, interface_id="TestVerworfen", label="x", proposed_by="pytest",
    )
    registry.reject_proposal(conn, "proposed_interface", str(p["id"]))
    assert not any(
        i["id"] == "TestVerworfen" for i in registry.list_interfaces(conn))


def test_amend_proposal_lifecycle(conn):
    # Proposal ohne Domain → Approve scheitert → reject → amend → approve
    p = registry.propose_predicate(
        conn, predicate_id="amend_pred", label="x", range_kind="string",
        cardinality="1:n", proposed_by="pytest",
    )
    with pytest.raises(RegistryError, match="domain"):
        registry.approve_predicate(conn, str(p["id"]))
    registry.reject_proposal(conn, "proposed_predicate", str(p["id"]))

    amended = registry.amend_proposal(conn, str(p["id"]), {"domain_type": "Person"})
    assert amended["status"] == "pending"       # rejected → pending
    assert amended["decided_at"] is None
    assert amended["domain_type"] == "Person"
    registry.approve_predicate(conn, str(p["id"]))

    # approved ist unveränderlich
    with pytest.raises(RegistryError, match="unveränderlich"):
        registry.amend_proposal(conn, str(p["id"]), {"label": "y"})

    # Felder außerhalb des Propose-Vokabulars sind nicht patchbar
    t = registry.propose_type(
        conn, type_id="AmendTyp", parent_id="Person", kind="continuant",
        label="x", proposed_by="pytest",
    )
    with pytest.raises(RegistryError, match="Unbekannte Felder"):
        registry.amend_proposal(conn, str(t["id"]), {"status": "approved"})


def test_bulk_propose_types_und_predicates(conn):
    from weltmodell.errors import ValidationError

    res = registry.propose_types(conn, items=[
        {"type_id": "BulkTyp1", "parent_id": "Person", "kind": "continuant",
         "label": "Bulk Eins"},
        {"type_id": "Person", "parent_id": "Person", "kind": "continuant",
         "label": "Dublette"},
    ], atomic=False)
    assert res["committed"] == 1
    assert res["results"][0]["ok"] is True
    assert res["results"][1]["ok"] is False

    # atomic=True: erster Fehler bricht mit Item-Index ab
    with pytest.raises(ValidationError, match="Item 0"):
        registry.propose_types(conn, items=[
            {"type_id": "Person", "parent_id": "Person", "kind": "continuant",
             "label": "Dublette"},
        ], atomic=True)
    conn.rollback()

    res = registry.propose_predicates(conn, items=[
        {"predicate_id": "bulk_pred_1", "label": "x", "range_kind": "string",
         "domain_type": "Person", "cardinality": "1:n"},
    ], atomic=True)
    assert res["committed"] == 1


def test_propose_failt_frueh_bei_shape_verstoessen(conn):
    with pytest.raises(RegistryError, match="range_kind"):
        registry.propose_predicate(
            conn, predicate_id="quatsch_range", label="x", range_kind="farbe",
            domain_type="Person", cardinality="1:1", proposed_by="test",
        )
    with pytest.raises(RegistryError, match="identifying"):
        registry.propose_predicate(
            conn, predicate_id="quatsch_ident", label="x", range_kind="string",
            domain_type="Person", cardinality="1:n", identifying=True,
            proposed_by="test",
        )
    with pytest.raises(RegistryError, match="kind"):
        registry.propose_type(
            conn, type_id="QuatschKind", parent_id="Person", kind="dings",
            label="x", proposed_by="test",
        )


def test_list_proposals_validiert_status(conn):
    with pytest.raises(RegistryError, match="status"):
        registry.list_proposals(conn, status="quatsch")


def test_stats_zaehlt_interface_proposals(conn):
    from weltmodell.queries import stats

    before = stats(conn)["pending_proposals"]
    registry.propose_interface(conn, interface_id="StatsZaehlIface", label="x",
                               proposed_by="test")
    assert stats(conn)["pending_proposals"] == before + 1


def test_person_namen_praedikate_geseedet(conn):
    # Migration 0017: vorname/nachname als Person-Attribute
    for pid, wd in (("vorname", "P735"), ("nachname", "P734")):
        p = registry.get_predicate(conn, pid)
        assert p is not None, f"{pid} fehlt (Migration 0017)"
        assert p["domain_type"] == "Person"
        assert p["range_kind"] == "string"
        assert p["cardinality"] == "1:1"
        assert p["identifying"] is False
        assert p["wikidata_pid"] == wd
