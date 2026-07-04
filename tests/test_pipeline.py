"""KI-Fill-Pipeline (Spec §7, §9): Ingest → Extract → Resolve → Validate → Commit,
Review-Gate für unbekannte Prädikate, Dedup über Quellen hinweg.

Minimalmodell: nur Person und SocialMediaAccount."""

from weltmodell import registry
from weltmodell.pipeline import ingest_document, run_pipeline
from weltmodell.queries import entity_view, semantic_search

# Eigene Identität (eindeutige E-Mail/Namen), damit das deterministische Dedup
# nicht mit Entities aus anderen Tests (z. B. dem nicht-deterministischen
# LLM-Test) über gemeinsame Identity-Keys verschmilzt.
PROFILE = {
    "kind": "social_profile",
    "name": "Pipeline Beispielperson",
    "email": "pipeline-beispiel@example.org",
    "aliases": ["P. Beispiel"],
    "knows": [{"name": "Pipeline Kontakt", "since": "2020-05-01"}],
    "accounts": [
        {"platform": "linkedin", "handle": "pbeispiel",
         "uri": "linkedin.com/in/pbeispiel",
         "follows": ["twitter.com/pkontakt"]},
    ],
    "favorite_food": "Ramen",  # unbekanntes Feld → muss ins Gate
}


def _find_person(conn, label="Pipeline Beispielperson"):
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

    # Name, E-Mail, Alias auf der Person (Nameable + Identity-Key)
    assert by_pred["email"][0]["value_text"] == "pipeline-beispiel@example.org"
    assert by_pred["alias"][0]["value_text"] == "P. Beispiel"

    # knows-Kante: „seit" wird Valid-Time (valid_from, §3), Provenance überall (§9)
    knows = by_pred["knows"][0]
    assert knows["object_label"] == "Pipeline Kontakt"
    assert str(knows["valid_from"]).startswith("2020-05-01")
    assert knows["references"][0]["activity"] == "apify:linkedin"

    # owns_account → SocialMediaAccount; darauf handle/platform/follows
    account_stmt = by_pred["owns_account"][0]
    assert account_stmt["object_type"] == "SocialMediaAccount"
    acc_view = entity_view(conn, str(account_stmt["object_id"]))
    acc_preds = {s["predicate_id"] for s in acc_view["statements"]}
    assert {"handle", "platform", "follows"} <= acc_preds

    # follows zeigt Account → Account (§9)
    follows = next(s for s in acc_view["statements"] if s["predicate_id"] == "follows")
    assert follows["object_type"] == "SocialMediaAccount"

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
        "name": "P. Beispiel (andere Quelle)",
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
