"""Statement-Write-Path (Spec §3–§7): Shape-Check, Provenance-Pflicht,
Bitemporalität, Widerspruchs-Koexistenz."""

import pytest

from weltmodell.entities import create_entity
from weltmodell.errors import ValidationError
from weltmodell.queries import entity_view
from weltmodell.statements import commit_statement, deprecate_statement


@pytest.fixture
def person(conn):
    return str(create_entity(conn, type_id="Person", label="Testperson Alpha")["id"])


@pytest.fixture
def org(conn):
    return str(create_entity(conn, type_id="Organization", label="Testorg GmbH")["id"])


def test_reject_unknown_predicate(conn, person, source_id):
    # §7.1: der Extraktor (und jeder andere) erfindet keine Prädikate
    with pytest.raises(ValidationError, match="Unbekanntes Prädikat"):
        commit_statement(
            conn, subject_id=person, predicate_id="works_at_as_werkstudent",
            value={"type": "string", "text": "x"}, source_ids=[source_id],
        )


def test_reject_without_provenance(conn, person, org):
    # Invariante 3: Kein Fakt ohne Provenance
    with pytest.raises(ValidationError, match="Provenance"):
        commit_statement(
            conn, subject_id=person, predicate_id="works_at",
            value={"type": "entity", "object_id": org}, source_ids=[],
        )


def test_reject_domain_violation(conn, org, person, source_id):
    # 'knows' verlangt Person als Subjekt
    with pytest.raises(ValidationError, match="Domain-Verstoß"):
        commit_statement(
            conn, subject_id=org, predicate_id="knows",
            value={"type": "entity", "object_id": person}, source_ids=[source_id],
        )


def test_reject_range_violation(conn, person, source_id):
    # 'works_at' verlangt Organization als Objekt
    other = str(create_entity(conn, type_id="Person", label="Testperson Beta")["id"])
    with pytest.raises(ValidationError, match="Range-Verstoß"):
        commit_statement(
            conn, subject_id=person, predicate_id="works_at",
            value={"type": "entity", "object_id": other}, source_ids=[source_id],
        )


def test_commit_with_qualifiers_and_reference(conn, person, org, source_id):
    # Worked Example s1 (§9): works_at + role/hours-Qualifier
    row = commit_statement(
        conn, subject_id=person, predicate_id="works_at",
        value={"type": "entity", "object_id": org}, source_ids=[source_id],
        valid_from="2024-10-01",
        qualifiers=[
            {"predicate_id": "role", "value": {"type": "string", "text": "Werkstudent"}},
            {"predicate_id": "hours", "value": {"type": "number", "number": 16}},
        ],
    )
    view = entity_view(conn, person)
    stmt = next(s for s in view["statements"] if s["predicate_id"] == "works_at")
    assert {q["predicate_id"] for q in stmt["qualifiers"]} == {"role", "hours"}
    assert stmt["references"][0]["activity"] == "test:fixture"
    assert row["flags"] == []


def test_quantity_and_geo_values(conn, source_id):
    # §3.1 Wert-Polymorphie: eine Struktur für Kurs und Koordinaten
    index = str(create_entity(conn, type_id="StockIndex", label="TESTDAX")["id"])
    commit_statement(
        conn, subject_id=index, predicate_id="price",
        value={"type": "quantity", "number": 142.30, "unit": "EUR"},
        source_ids=[source_id],
    )
    country = str(create_entity(conn, type_id="Country", label="Testland")["id"])
    commit_statement(
        conn, subject_id=country, predicate_id="coordinates",
        value={"type": "geo", "lat": 23.7, "lon": 121.0}, source_ids=[source_id],
    )
    view = entity_view(conn, index)
    price = next(s for s in view["statements"] if s["predicate_id"] == "price")
    assert float(price["value_number"]) == 142.30 and price["value_unit"] == "EUR"
    geo = next(
        s for s in entity_view(conn, country)["statements"]
        if s["predicate_id"] == "coordinates"
    )
    assert geo["value_geojson"]["coordinates"] == [121.0, 23.7]


def test_cardinality_conflict_is_flag_not_reject(conn, source_id):
    # §6: Widersprüche koexistieren — Kardinalität flaggt nur
    account = str(create_entity(conn, type_id="Account", label="@testhandle")["id"])
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


def test_contradiction_coexists_via_rank_and_bitemporality(conn, person, org, source_id):
    """§6/§9: kein Overwrite, kein Datenverlust — deprecate + preferred."""
    old = commit_statement(
        conn, subject_id=person, predicate_id="works_at",
        value={"type": "entity", "object_id": org}, source_ids=[source_id],
        valid_from="2024-10-01",
    )
    conn.commit()  # eigene Transaktion, damit system_from-Zeitachsen trennbar sind
    t_between = conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    conn.commit()  # now() ist Transaktionsstart — Korrektur braucht eigene Transaktion

    deprecate_statement(conn, str(old["id"]), valid_to="2027-01-31")
    new_org = str(create_entity(conn, type_id="Organization", label="Neue Org AG")["id"])
    commit_statement(
        conn, subject_id=person, predicate_id="works_at",
        value={"type": "entity", "object_id": new_org}, source_ids=[source_id],
        rank="preferred", valid_from="2027-02-01",
    )
    conn.commit()

    # Aktuelle Sicht: nur der neue Arbeitgeber
    current = entity_view(conn, person)
    works = [s for s in current["statements"] if s["predicate_id"] == "works_at"]
    assert len(works) == 1
    assert str(works[0]["object_id"]) == new_org
    assert works[0]["rank"] == "preferred"

    # Historie bleibt vollständig (include_deprecated)
    full = entity_view(conn, person, include_deprecated=True)
    all_works = [s for s in full["statements"] if s["predicate_id"] == "works_at"]
    assert {s["rank"] for s in all_works} == {"preferred", "deprecated"}

    # §4 Achse 2: „Was habe ich am Datum D geglaubt?" — vor der Korrektur
    belief = entity_view(conn, person, system_at=t_between)
    old_works = [s for s in belief["statements"] if s["predicate_id"] == "works_at"]
    assert len(old_works) == 1
    assert str(old_works[0]["object_id"]) == str(org)
    assert old_works[0]["rank"] == "normal"
