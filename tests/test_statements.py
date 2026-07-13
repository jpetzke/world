"""Statement-Write-Path (Spec §3–§7): Shape-Check, Provenance-Pflicht,
Bitemporalität, Widerspruchs-Koexistenz.

Minimalmodell: nur Person und SocialMediaAccount."""

import pytest

from weltmodell.entities import create_entities, create_entity, get_entity
from weltmodell.errors import ValidationError
from weltmodell.queries import entity_view
from weltmodell.statements import (
    commit_statement,
    commit_statements,
    deprecate_statement,
    fix_statement,
    supersede_statement,
)


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


# --- Label-Cache: ableitbar, jederzeit neu berechenbar (Invariante 1) ---------


def test_label_cache_recomputes_on_preferred_commit(conn, source_id):
    # entity.label folgt dem besten name-Statement, nicht dem create-Argument
    pid = str(create_entity(conn, type_id="Person", label="Jonas")["id"])
    commit_statement(
        conn, subject_id=pid, predicate_id="name",
        value={"type": "string", "text": "Jonas"}, source_ids=[source_id],
        confidence=0.6,
    )
    assert get_entity(conn, pid)["label"] == "Jonas"
    # Korrektur mit rank=preferred schlägt durch (preferred vor normal)
    commit_statement(
        conn, subject_id=pid, predicate_id="name",
        value={"type": "string", "text": "Jonas Petzke"}, source_ids=[source_id],
        rank="preferred",
    )
    assert get_entity(conn, pid)["label"] == "Jonas Petzke"


def test_label_cache_follows_rank_change(conn, source_id):
    # Rank-Wechsel via set_rank (supersede) verschiebt die preferred-Wahl → Cache
    pid = str(create_entity(conn, type_id="Person", label="Alt")["id"])
    a = commit_statement(
        conn, subject_id=pid, predicate_id="name",
        value={"type": "string", "text": "Alt Name"}, source_ids=[source_id],
        confidence=0.5,
    )
    commit_statement(
        conn, subject_id=pid, predicate_id="name",
        value={"type": "string", "text": "Neu Name"}, source_ids=[source_id],
        confidence=0.9,
    )
    assert get_entity(conn, pid)["label"] == "Neu Name"  # höhere Confidence
    supersede_statement(conn, str(a["id"]), rank="preferred")
    assert get_entity(conn, pid)["label"] == "Alt Name"  # preferred schlägt Confidence


def test_label_cache_after_deprecate(conn, source_id):
    pid = str(create_entity(conn, type_id="Person", label="X")["id"])
    a = commit_statement(
        conn, subject_id=pid, predicate_id="name",
        value={"type": "string", "text": "First"}, source_ids=[source_id],
        confidence=0.9,
    )
    commit_statement(
        conn, subject_id=pid, predicate_id="name",
        value={"type": "string", "text": "Second"}, source_ids=[source_id],
        confidence=0.5,
    )
    assert get_entity(conn, pid)["label"] == "First"
    deprecate_statement(conn, str(a["id"]))
    assert get_entity(conn, pid)["label"] == "Second"  # First deprecated → Second


# --- Bulk-Operationen ----------------------------------------------------------


def test_bulk_create_entities(conn):
    out = create_entities(conn, items=[
        {"type_id": "Person", "label": "Bulk A"},
        {"type_id": "Person", "label": "Bulk B"},
    ])
    assert out["total"] == 2 and out["committed"] == 2
    assert all(r["ok"] for r in out["results"])
    assert out["results"][0]["label"] == "Bulk A"


def test_bulk_create_entities_atomic_aborts_with_index(conn):
    with pytest.raises(ValidationError, match="Item 1"):
        create_entities(conn, items=[
            {"type_id": "Person", "label": "Good"},
            {"type_id": "GibtEsNicht", "label": "Bad"},
        ], atomic=True)


def test_bulk_commit_best_effort_isolates_failures(conn, source_id):
    pid = str(create_entity(conn, type_id="Person", label="Bulk Subj")["id"])
    out = commit_statements(conn, items=[
        {"subject_id": pid, "predicate_id": "name",
         "value": {"type": "string", "text": "ok1"}, "source_ids": [source_id]},
        {"subject_id": pid, "predicate_id": "erfundenes_praedikat",
         "value": {"type": "string", "text": "x"}, "source_ids": [source_id]},
        {"subject_id": pid, "predicate_id": "name",
         "value": {"type": "string", "text": "ok2"}, "source_ids": [source_id]},
    ], atomic=False)
    assert out["committed"] == 2
    assert [r["ok"] for r in out["results"]] == [True, False, True]
    assert "erfundenes_praedikat" in out["results"][1]["error"]
    # Die gültigen zwei sind wirklich persistiert (Savepoint isoliert nur den Fehler)
    names = conn.execute(
        "SELECT count(*) AS n FROM statement WHERE subject_id = %s "
        "AND predicate_id = 'name' AND system_to IS NULL", (pid,)
    ).fetchone()["n"]
    assert names == 2


# --- fix: Erratum-Korrektur (bricht bewusst Invariante 4) ----------------------


def test_fix_overwrites_value_in_place(conn, source_id):
    pid = str(create_entity(conn, type_id="Person", label="Fix Subj")["id"])
    row = commit_statement(
        conn, subject_id=pid, predicate_id="name",
        value={"type": "string", "text": "Tpyo"}, source_ids=[source_id],
    )
    fixed = fix_statement(
        conn, str(row["id"]), reason="Tippfehler",
        value={"type": "string", "text": "Typo"},
    )
    assert fixed["value_text"] == "Typo"
    assert str(fixed["id"]) == str(row["id"])  # gleiche Zeile, kein neuer Versionssatz
    # Kein bitemporaler Zwilling — genau eine Zeile mit dieser id
    n = conn.execute(
        "SELECT count(*) AS n FROM statement WHERE id = %s", (str(row["id"]),)
    ).fetchone()["n"]
    assert n == 1
    assert get_entity(conn, pid)["label"] == "Typo"  # Label-Cache folgt dem Fix


def test_fix_delete_removes_statement_and_children(conn, source_id):
    pid = str(create_entity(conn, type_id="Person", label="Del Subj")["id"])
    row = commit_statement(
        conn, subject_id=pid, predicate_id="knows",
        value={"type": "entity",
               "object_id": str(create_entity(conn, type_id="Person",
                                              label="Other")["id"])},
        source_ids=[source_id],
        qualifiers=[{"predicate_id": "beginn",
                     "value": {"type": "datetime", "datetime": "2020-01-01"}}],
    )
    out = fix_statement(conn, str(row["id"]), reason="versehentlich", delete=True)
    assert out["deleted"] is True
    assert conn.execute(
        "SELECT count(*) AS n FROM statement WHERE id = %s", (str(row["id"]),)
    ).fetchone()["n"] == 0
    assert conn.execute(  # ON DELETE CASCADE räumt Qualifier mit weg
        "SELECT count(*) AS n FROM qualifier WHERE statement_id = %s", (str(row["id"]),)
    ).fetchone()["n"] == 0


def test_fix_revalidates_against_registry(conn, source_id):
    # Ein Fix darf nie ein ungültiges Statement erzeugen: name erwartet string
    pid = str(create_entity(conn, type_id="Person", label="RV")["id"])
    row = commit_statement(
        conn, subject_id=pid, predicate_id="name",
        value={"type": "string", "text": "ok"}, source_ids=[source_id],
    )
    with pytest.raises(ValidationError, match="Range-Verstoß"):
        fix_statement(conn, str(row["id"]), reason="x",
                      value={"type": "number", "number": 5})


def test_fix_requires_reason(conn, source_id):
    pid = str(create_entity(conn, type_id="Person", label="RR")["id"])
    row = commit_statement(
        conn, subject_id=pid, predicate_id="name",
        value={"type": "string", "text": "ok"}, source_ids=[source_id],
    )
    with pytest.raises(ValidationError, match="reason"):
        fix_statement(conn, str(row["id"]), reason="   ", rank="preferred")


# --- Paket 2: Qualifier-Validierung + Entity-Erratum -------------------------


def test_qualifier_validiert_range_kind_nicht_domain(conn, person, source_id):
    other = str(create_entity(conn, type_id="Person", label="Qualifier Ziel")["id"])
    # Domain-Check ist für Qualifier BEWUSST ausgesetzt: beginn trägt Domain
    # Ereignis, ist aber als Zeit-Qualifier an einem knows-Statement legitim.
    row = commit_statement(
        conn, subject_id=person, predicate_id="knows",
        value={"type": "entity", "object_id": other}, source_ids=[source_id],
        qualifiers=[{"predicate_id": "beginn",
                     "value": {"type": "datetime",
                               "datetime": "2020-01-01T00:00:00Z"}}],
    )
    quals = conn.execute(
        "SELECT * FROM qualifier WHERE statement_id = %s", (row["id"],)
    ).fetchall()
    assert len(quals) == 1

    # range_kind wird dagegen validiert: beginn (datetime) mit string-Wert
    with pytest.raises(ValidationError, match="Qualifier-Range-Verstoß"):
        commit_statement(
            conn, subject_id=person, predicate_id="knows",
            value={"type": "entity", "object_id": other}, source_ids=[source_id],
            qualifiers=[{"predicate_id": "beginn",
                         "value": {"type": "string", "text": "früher"}}],
        )


def test_fix_entity_loescht_leeren_anker(conn):
    from weltmodell.entities import fix_entity
    from weltmodell.errors import NotFoundError

    e = str(create_entity(conn, type_id="Person", label="Erratum Anker")["id"])
    res = fix_entity(conn, e, reason="versehentlich angelegt (Test)")
    assert res["deleted"] is True
    assert res["reason"] == "versehentlich angelegt (Test)"
    with pytest.raises(NotFoundError):
        get_entity(conn, e)


def test_fix_entity_blockt_benutzte_anker(conn, person, source_id):
    from weltmodell.entities import fix_entity

    commit_statement(
        conn, subject_id=person, predicate_id="name",
        value={"type": "string", "text": "In Benutzung"}, source_ids=[source_id],
    )
    with pytest.raises(ValidationError, match="welt_merge_entities"):
        fix_entity(conn, person, reason="soll scheitern")

    e = str(create_entity(conn, type_id="Person", label="Ohne Grund")["id"])
    with pytest.raises(ValidationError, match="reason"):
        fix_entity(conn, e, reason="   ")


def test_qualifier_quantity_mit_unit(conn, source_id):
    # Wikidata-Praxis (z. B. P1114 „Anzahl" als Qualifier): quantity ist als
    # Qualifier zulässig; die unit wandert mit (auch durch Supersession).
    unternehmen = str(create_entity(conn, type_id="Unternehmen", label="Quali AG")["id"])
    wertpapier = str(create_entity(conn, type_id="Wertpapier", label="Quali Aktie")["id"])
    row = commit_statement(
        conn, subject_id=wertpapier, predicate_id="emittiert_von",
        value={"type": "entity", "object_id": unternehmen},
        source_ids=[source_id],
        qualifiers=[{"predicate_id": "kaufpreis",
                     "value": {"type": "quantity", "number": 100, "unit": "EUR"}}],
    )
    q = conn.execute(
        "SELECT * FROM qualifier WHERE statement_id = %s", (row["id"],)
    ).fetchone()
    assert q["value_type"] == "quantity"
    assert float(q["value_number"]) == 100.0
    assert q["value_unit"] == "EUR"

    new = supersede_statement(conn, str(row["id"]), rank="preferred")
    q2 = conn.execute(
        "SELECT * FROM qualifier WHERE statement_id = %s", (new["id"],)
    ).fetchone()
    assert q2["value_unit"] == "EUR"


def test_meta_validierung_vor_db_check(conn, person, source_id):
    # Klare ValidationError statt roher CheckViolation aus der DB
    for kwargs, msg in (
        ({"rank": "superduper"}, "rank"),
        ({"origin": "erfunden"}, "origin"),
        ({"confidence": 1.5}, "confidence"),
    ):
        with pytest.raises(ValidationError, match=msg):
            commit_statement(
                conn, subject_id=person, predicate_id="name",
                value={"type": "string", "text": "Meta"},
                source_ids=[source_id], **kwargs,
            )


def test_leeres_gueltigkeitsfenster_rejected(conn, person, source_id):
    with pytest.raises(ValidationError, match="Gültigkeitsfenster"):
        commit_statement(
            conn, subject_id=person, predicate_id="name",
            value={"type": "string", "text": "Fenster"},
            source_ids=[source_id],
            valid_from="2025-01-01T00:00:00Z", valid_to="2020-01-01T00:00:00Z",
        )


def test_identifying_konflikt_klare_meldung(conn, source_id):
    a = str(create_entity(conn, type_id="Person", label="Ident A")["id"])
    b = str(create_entity(conn, type_id="Person", label="Ident B")["id"])
    commit_statement(
        conn, subject_id=a, predicate_id="email",
        value={"type": "string", "text": "ident-konflikt@example.org"},
        source_ids=[source_id],
    )
    with pytest.raises(ValidationError, match="welt_merge_entities"):
        commit_statement(
            conn, subject_id=b, predicate_id="email",
            value={"type": "string", "text": "ident-konflikt@example.org"},
            source_ids=[source_id],
        )


def test_identische_behauptung_wird_rebestaetigt(conn, person, source_id):
    # Snapshot-Philosophie generalisiert: exakt identische Behauptung aus
    # neuer Quelle → Reference ans bestehende Statement, kein Duplikat.
    first = commit_statement(
        conn, subject_id=person, predicate_id="name",
        value={"type": "string", "text": "Duplikat Dora"}, source_ids=[source_id],
    )
    from weltmodell.pipeline import ingest_document

    src2 = str(ingest_document(
        conn, raw={}, url=None, activity="test:zweite-quelle", agent="pytest",
    )["id"])
    second = commit_statement(
        conn, subject_id=person, predicate_id="name",
        value={"type": "string", "text": "Duplikat Dora"}, source_ids=[src2],
    )
    assert str(second["id"]) == str(first["id"])
    assert second["flags"] == ["reconfirmed"]
    refs = conn.execute(
        "SELECT count(*) AS n FROM reference WHERE statement_id = %s",
        (first["id"],),
    ).fetchone()["n"]
    assert refs == 2
    # Anderes Gültigkeitsfenster bleibt eine EIGENE Behauptung
    third = commit_statement(
        conn, subject_id=person, predicate_id="name",
        value={"type": "string", "text": "Duplikat Dora"}, source_ids=[src2],
        valid_from="2020-01-01T00:00:00Z",
    )
    assert str(third["id"]) != str(first["id"])


def test_fix_zaehlt_sich_nicht_als_kardinalitaetskonflikt(conn, source_id):
    acc = str(create_entity(
        conn, type_id="SocialMediaAccount", label="@kardfix")["id"])
    row = commit_statement(
        conn, subject_id=acc, predicate_id="handle",
        value={"type": "string", "text": "kardfix"}, source_ids=[source_id],
    )
    fixed = fix_statement(
        conn, str(row["id"]), reason="Tippfehler",
        value={"type": "string", "text": "kardfix2"},
    )
    assert fixed["flags"] == []


def test_unknown_predicate_error_suggests_candidates(conn, source_id):
    person = str(create_entity(conn, type_id="Person", label="Vorschlag Test")["id"])
    with pytest.raises(ValidationError) as exc:
        commit_statement(
            conn, subject_id=person, predicate_id="Name",
            value={"type": "string", "text": "x"}, source_ids=[source_id],
        )
    assert "'name'" in str(exc.value)  # meintest du 'name'?


# --- Audit-Fixes: Fehlertexte, Qualifier, Bulk-Robustheit --------------------


def test_value_type_error_lists_valid_kinds(conn, person, source_id):
    with pytest.raises(ValidationError, match="string"):
        commit_statement(
            conn, subject_id=person, predicate_id="name",
            value={"type": "str", "text": "x"}, source_ids=[source_id],
        )


def test_qualifier_unknown_predicate_suggests(conn, person, source_id):
    other = str(create_entity(conn, type_id="Person", label="Qual Vorschlag")["id"])
    with pytest.raises(ValidationError, match="'beginn'"):
        commit_statement(
            conn, subject_id=person, predicate_id="knows",
            value={"type": "entity", "object_id": other}, source_ids=[source_id],
            qualifiers=[{"predicate_id": "Beginn",
                         "value": {"type": "datetime", "datetime": "2020-01-01"}}],
        )


def test_qualifier_object_id_canonicalized(conn, person, source_id):
    from weltmodell.resolution import merge_entity

    a = str(create_entity(conn, type_id="Person", label="Qual Merge Quelle")["id"])
    b = str(create_entity(conn, type_id="Person", label="Qual Merge Ziel")["id"])
    merge_entity(conn, a, b)
    other = str(create_entity(conn, type_id="Person", label="Qual Kante")["id"])
    row = commit_statement(
        conn, subject_id=person, predicate_id="knows",
        value={"type": "entity", "object_id": other}, source_ids=[source_id],
        qualifiers=[{"predicate_id": "knows",
                     "value": {"type": "entity", "object_id": a}}],
    )
    q = conn.execute("SELECT object_id FROM qualifier WHERE statement_id = %s",
                     (row["id"],)).fetchone()
    assert str(q["object_id"]) == b


def test_bulk_best_effort_survives_db_error(conn, person, source_id):
    res = commit_statements(conn, items=[
        {"subject_id": person, "predicate_id": "knows",
         "value": {"type": "entity", "object_id": "keine-uuid"},
         "source_ids": [source_id]},
        {"subject_id": person, "predicate_id": "name",
         "value": {"type": "string", "text": "Bulk Survivor"},
         "source_ids": [source_id]},
    ], atomic=False)
    assert res["committed"] == 1
    assert res["results"][0]["ok"] is False
    assert res["results"][1]["ok"] is True


def test_bulk_atomic_db_error_names_index(conn, person, source_id):
    with pytest.raises(ValidationError, match="Item 1"):
        commit_statements(conn, items=[
            {"subject_id": person, "predicate_id": "name",
             "value": {"type": "string", "text": "ok"}, "source_ids": [source_id]},
            {"subject_id": person, "predicate_id": "knows",
             "value": {"type": "entity", "object_id": "keine-uuid"},
             "source_ids": [source_id]},
        ], atomic=True)


def test_bulk_missing_field_names_item(conn):
    with pytest.raises(ValidationError, match="Item 0"):
        commit_statements(conn, items=[{"predicate_id": "name"}], atomic=True)


def test_create_entity_warns_on_exact_duplicate(conn):
    create_entity(conn, type_id="Person", label="Dublette Warnung")
    second = create_entity(conn, type_id="Person", label="dublette warnung")
    assert any("Dublette" in w for w in second.get("warnings", []))
