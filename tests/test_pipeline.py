"""KI-Fill-Pipeline (Spec §7, §9): Ingest → Extract → Resolve → Validate → Commit,
Review-Gate für unbekannte Prädikate, Dedup über Quellen hinweg."""

from weltmodell import registry
from weltmodell.pipeline import ingest_document, run_pipeline
from weltmodell.queries import entity_view, semantic_search

PROFILE = {
    "kind": "social_profile",
    "name": "Jonas Beispiel",
    "email": "jonas@example.org",
    "employer": {"name": "BLAID Beispiel GmbH", "role": "Werkstudent",
                 "hours": 16, "since": "2024-10-01"},
    "partner": "Tanja Beispiel",
    "accounts": [{"platform": "linkedin", "handle": "jbeispiel",
                  "uri": "linkedin.com/in/jbeispiel"}],
    "mentions": [{"text": "Jonas Beispiel über Weltmodelle interviewt",
                  "date": "2026-06-01"}],
    "favorite_food": "Ramen",  # unbekanntes Feld → muss ins Gate
}


def _find_person(conn, label="Jonas Beispiel"):
    hits = semantic_search(conn, label, type_id="Person", limit=3)
    return next(h for h in hits if h["label"] == label)


def test_pipeline_worked_example(conn):
    doc = ingest_document(
        conn, raw=PROFILE, url="https://example.org/profil",
        activity="apify:linkedin", agent="pytest-pipeline",
    )
    report = run_pipeline(conn, source_id=str(doc["id"]), agent="pytest-pipeline")

    assert report["committed"], "Statements erwartet"
    assert not report["rejected"], f"Unerwartete Rejects: {report['rejected']}"

    person = _find_person(conn)
    view = entity_view(conn, person["id"])
    by_pred = {}
    for s in view["statements"]:
        by_pred.setdefault(s["predicate_id"], []).append(s)

    # Worked Example §9: works_at + Qualifier, Provenance überall
    works = by_pred["works_at"][0]
    assert works["object_label"] == "BLAID Beispiel GmbH"
    assert {q["predicate_id"] for q in works["qualifiers"]} == {"role", "hours"}
    assert works["references"][0]["activity"] == "apify:linkedin"
    assert by_pred["romantic_partner_of"][0]["object_label"] == "Tanja Beispiel"
    assert by_pred["owns_account"][0]["object_type"] == "Account"

    # Mention ist ein Occurrent, das auf die Person zeigt (§1.1, §9)
    mention = next(
        s for s in view["incoming"] if s["predicate_id"] == "mentions"
    )
    assert mention is not None

    # favorite_food wurde NICHT geschrieben, sondern vorgeschlagen (§7.1)
    assert "favorite_food" not in by_pred
    assert any(p["predicate_id"] == "favorite_food"
               for p in registry.list_proposals(conn)["predicates"])


def test_pipeline_dedups_across_sources(conn):
    doc1 = ingest_document(
        conn, raw=PROFILE, activity="apify:linkedin", agent="pytest-pipeline",
    )
    run_pipeline(conn, source_id=str(doc1["id"]))
    person_before = _find_person(conn)["id"]

    # Zweite Quelle, gleiche E-Mail → kein Duplikat
    variant = {
        "kind": "social_profile",
        "name": "J. Beispiel",
        "email": PROFILE["email"],
    }
    doc2 = ingest_document(
        conn, raw=variant, activity="scrapling:web", agent="pytest-pipeline",
    )
    report2 = run_pipeline(conn, source_id=str(doc2["id"]))

    persons = [
        e for e in report2["entities_created"]
        if conn.execute("SELECT type_id FROM entity WHERE id = %s", (e,))
        .fetchone()["type_id"] == "Person"
    ]
    assert persons == [], "Deterministisches Dedup: kein neuer Person-Anker"
    assert _find_person(conn)["id"] == person_before


def test_gate_approval_closes_flywheel(conn):
    """Nach Approve mappt der nächste Lauf das Feld auf das neue Prädikat."""
    pending = [
        p for p in registry.list_proposals(conn)["predicates"]
        if p["predicate_id"] == "favorite_food"
    ]
    assert pending, "Proposal aus vorherigem Test erwartet"
    registry.approve_predicate(conn, str(pending[0]["id"]))
    assert registry.get_predicate(conn, "favorite_food") is not None

    doc = ingest_document(
        conn, raw=PROFILE, activity="apify:linkedin", agent="pytest-pipeline",
    )
    report = run_pipeline(conn, source_id=str(doc["id"]))
    assert not report["rejected"]

    person = _find_person(conn)
    view = entity_view(conn, person["id"])
    food = [s for s in view["statements"] if s["predicate_id"] == "favorite_food"]
    assert food and food[0]["value_text"] == "Ramen"
