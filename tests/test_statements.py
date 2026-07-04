"""Statement-Write-Path (Spec §3–§7): Shape-Check, Provenance-Pflicht,
Bitemporalität, Widerspruchs-Koexistenz.

Minimalmodell: nur Person und SocialMediaAccount."""

import pytest

from weltmodell.entities import create_entity
from weltmodell.errors import ValidationError
from weltmodell.queries import entity_view
from weltmodell.statements import commit_statement, deprecate_statement


@pytest.fixture
def person(conn):
    return str(create_entity(conn, type_id="Person", label="Testperson Alpha")["id"])


@pytest.fixture
def account(conn):
    return str(create_entity(
        conn, type_id="SocialMediaAccount", label="@testaccount")["id"])


def test_reject_unknown_predicate(conn, person, source_id):
    # §7.1: der Extraktor (und jeder andere) erfindet keine Prädikate
    with pytest.raises(ValidationError, match="Unbekanntes Prädikat"):
        commit_statement(
            conn, subject_id=person, predicate_id="works_at_as_werkstudent",
            value={"type": "string", "text": "x"}, source_ids=[source_id],
        )


def test_reject_without_provenance(conn, person, account):
    # Invariante 3: Kein Fakt ohne Provenance
    with pytest.raises(ValidationError, match="Provenance"):
        commit_statement(
            conn, subject_id=person, predicate_id="owns_account",
            value={"type": "entity", "object_id": account}, source_ids=[],
        )


def test_reject_domain_violation(conn, account, person, source_id):
    # 'knows' verlangt Person als Subjekt — ein Account ist keins
    with pytest.raises(ValidationError, match="Domain-Verstoß"):
        commit_statement(
            conn, subject_id=account, predicate_id="knows",
            value={"type": "entity", "object_id": person}, source_ids=[source_id],
        )


def test_reject_range_violation(conn, person, source_id):
    # 'owns_account' verlangt einen SocialMediaAccount als Objekt
    other = str(create_entity(conn, type_id="Person", label="Testperson Beta")["id"])
    with pytest.raises(ValidationError, match="Range-Verstoß"):
        commit_statement(
            conn, subject_id=person, predicate_id="owns_account",
            value={"type": "entity", "object_id": other}, source_ids=[source_id],
        )


def test_commit_with_qualifiers_and_reference(conn, person, source_id):
    # knows-Kante mit Zeit-Qualifier: Registry-Prädikate sind dual nutzbar
    # (Wikidata-Praxis, P580 als Qualifier); „seit wann gilt" wäre valid_from (§3)
    other = str(create_entity(conn, type_id="Person", label="Testperson Gamma")["id"])
    row = commit_statement(
        conn, subject_id=person, predicate_id="knows",
        value={"type": "entity", "object_id": other}, source_ids=[source_id],
        qualifiers=[
            {"predicate_id": "beginn", "value": {"type": "datetime",
                                                 "datetime": "2020-05-01"}},
        ],
    )
    view = entity_view(conn, person)
    stmt = next(s for s in view["statements"] if s["predicate_id"] == "knows")
    assert {q["predicate_id"] for q in stmt["qualifiers"]} == {"beginn"}
    assert stmt["references"][0]["activity"] == "test:fixture"
    assert row["flags"] == []


def test_cardinality_conflict_is_flag_not_reject(conn, source_id):
    # §6: Widersprüche koexistieren — Kardinalität flaggt nur (handle ist 1:1)
    account = str(create_entity(
        conn, type_id="SocialMediaAccount", label="@testhandle")["id"])
    first = commit_statement(
        conn, subject_id=account, predicate_id="handle",
        value={"type": "string", "text": "testhandle"}, source_ids=[source_id],
    )
    second = commit_statement(
        conn, subject_id=account, predicate_id="handle",
        value={"type": "string", "text": "other_handle"}, source_ids=[source_id],
    )
    assert first["flags"] == []
    assert second["flags"] == ["cardinality_conflict_1:1"]


def test_contradiction_coexists_via_rank_and_bitemporality(conn, person, source_id):
    """§6/§9: kein Overwrite, kein Datenverlust — deprecate + preferred."""
    acc_old = str(create_entity(
        conn, type_id="SocialMediaAccount", label="@altaccount")["id"])
    old = commit_statement(
        conn, subject_id=person, predicate_id="owns_account",
        value={"type": "entity", "object_id": acc_old}, source_ids=[source_id],
        valid_from="2024-10-01",
    )
    conn.commit()  # eigene Transaktion, damit system_from-Zeitachsen trennbar sind
    t_between = conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    conn.commit()  # now() ist Transaktionsstart — Korrektur braucht eigene Transaktion

    deprecate_statement(conn, str(old["id"]), valid_to="2027-01-31")
    acc_new = str(create_entity(
        conn, type_id="SocialMediaAccount", label="@neuaccount")["id"])
    commit_statement(
        conn, subject_id=person, predicate_id="owns_account",
        value={"type": "entity", "object_id": acc_new}, source_ids=[source_id],
        rank="preferred", valid_from="2027-02-01",
    )
    conn.commit()

    # Aktuelle Sicht: nur der neue Account
    current = entity_view(conn, person)
    owns = [s for s in current["statements"] if s["predicate_id"] == "owns_account"]
    assert len(owns) == 1
    assert str(owns[0]["object_id"]) == acc_new
    assert owns[0]["rank"] == "preferred"

    # Historie bleibt vollständig (include_deprecated)
    full = entity_view(conn, person, include_deprecated=True)
    all_owns = [s for s in full["statements"] if s["predicate_id"] == "owns_account"]
    assert {s["rank"] for s in all_owns} == {"preferred", "deprecated"}

    # §4 Achse 2: „Was habe ich am Datum D geglaubt?" — vor der Korrektur
    belief = entity_view(conn, person, system_at=t_between)
    old_owns = [s for s in belief["statements"] if s["predicate_id"] == "owns_account"]
    assert len(old_owns) == 1
    assert str(old_owns[0]["object_id"]) == acc_old
    assert old_owns[0]["rank"] == "normal"


def test_finance_cross_domain_chain(conn, person, source_id):
    """§10-Kette real: Person → Unternehmen → Übernahme (Occurrent) über EINE Struktur."""
    firma = str(create_entity(conn, type_id="Unternehmen", label="TSMC")["id"])
    papier = str(create_entity(conn, type_id="Wertpapier", label="TSMC ADR")["id"])
    deal = str(create_entity(conn, type_id="Übernahme", label="Testübernahme")["id"])

    # Person arbeitet_bei Unternehmen (Range Organization, Subtyp erlaubt)
    # mit rolle-Qualifier statt works_at_as_X (§14.3)
    commit_statement(
        conn, subject_id=person, predicate_id="arbeitet_bei",
        value={"type": "entity", "object_id": firma}, source_ids=[source_id],
        qualifiers=[{"predicate_id": "rolle", "value": {"type": "string", "text": "CTO"}}],
    )
    # beteiligt_an mit anteil_prozent-Qualifier (Qualifier-only via Quantifiable-Domain)
    commit_statement(
        conn, subject_id=person, predicate_id="beteiligt_an",
        value={"type": "entity", "object_id": firma}, source_ids=[source_id],
        confidence=0.8,
        qualifiers=[{"predicate_id": "anteil_prozent",
                     "value": {"type": "number", "number": 5.9}}],
    )
    # Wertpapier: identifying isin + notiert_an mit ticker-Qualifier (P249 an P414)
    commit_statement(
        conn, subject_id=papier, predicate_id="isin",
        value={"type": "string", "text": "US8740391003"}, source_ids=[source_id],
    )
    commit_statement(
        conn, subject_id=papier, predicate_id="emittiert_von",
        value={"type": "entity", "object_id": firma}, source_ids=[source_id],
    )
    # Übernahme: n-äre Rollen + geerbtes beginn (Domain Ereignis) + quantity-Kaufpreis
    commit_statement(
        conn, subject_id=deal, predicate_id="übernahmeziel",
        value={"type": "entity", "object_id": firma}, source_ids=[source_id],
    )
    commit_statement(
        conn, subject_id=deal, predicate_id="käufer",
        value={"type": "entity", "object_id": person}, source_ids=[source_id],
    )
    commit_statement(
        conn, subject_id=deal, predicate_id="beginn",
        value={"type": "datetime", "datetime": "2026-01-15T00:00:00Z"},
        source_ids=[source_id],
    )
    commit_statement(
        conn, subject_id=deal, predicate_id="kaufpreis",
        value={"type": "quantity", "number": 1_000_000, "unit": "EUR"},
        source_ids=[source_id],
    )

    # wikidata_qid jetzt Nameable-weit: gilt für Unternehmen (vorher nur Ort)
    commit_statement(
        conn, subject_id=firma, predicate_id="wikidata_qid",
        value={"type": "string", "text": "Q713418"}, source_ids=[source_id],
    )

    view = entity_view(conn, deal)
    preds = {s["predicate_id"] for s in view["statements"]}
    assert {"übernahmeziel", "käufer", "beginn", "kaufpreis"} <= preds


def test_finance_range_enforced(conn, person, source_id):
    # übernahmeziel verlangt Unternehmen — eine Person ist keins
    deal = str(create_entity(conn, type_id="Übernahme", label="Fehlübernahme")["id"])
    with pytest.raises(ValidationError, match="Range-Verstoß"):
        commit_statement(
            conn, subject_id=deal, predicate_id="übernahmeziel",
            value={"type": "entity", "object_id": person}, source_ids=[source_id],
        )
