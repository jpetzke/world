"""API-Layer: die FastAPI-Actions sind der erzwungene Schreibweg (§7.1)."""


def _source(client):
    r = client.post("/sources", json={
        "activity": "test:api", "agent": "pytest-api",
        "url": "https://example.org/api",
    })
    assert r.status_code == 201
    return r.json()["id"]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_entity_statement_roundtrip(client):
    source = _source(client)
    person = client.post("/entities", json={
        "type_id": "Person", "label": "API Person",
    }).json()
    org = client.post("/entities", json={
        "type_id": "Organization", "label": "API Org",
    }).json()

    r = client.post("/statements", json={
        "subject_id": person["id"], "predicate_id": "works_at",
        "value": {"type": "entity", "object_id": org["id"]},
        "source_ids": [source],
        "qualifiers": [{"predicate_id": "role",
                        "value": {"type": "string", "text": "CTO"}}],
    })
    assert r.status_code == 201, r.text

    view = client.get(f"/entities/{person['id']}").json()
    works = next(s for s in view["statements"] if s["predicate_id"] == "works_at")
    assert works["object_label"] == "API Org"
    assert works["qualifiers"][0]["value_text"] == "CTO"


def test_api_rejects_gate_violations(client):
    source = _source(client)
    person = client.post("/entities", json={
        "type_id": "Person", "label": "API Reject Person",
    }).json()

    # Unbekanntes Prädikat → 422
    r = client.post("/statements", json={
        "subject_id": person["id"], "predicate_id": "made_up_predicate",
        "value": {"type": "string", "text": "x"}, "source_ids": [source],
    })
    assert r.status_code == 422

    # Unbekannter Typ → 422
    r = client.post("/entities", json={"type_id": "MadeUpType", "label": "x"})
    assert r.status_code == 422

    # Statement ohne Quelle scheitert schon am Payload-Schema
    r = client.post("/statements", json={
        "subject_id": person["id"], "predicate_id": "email",
        "value": {"type": "string", "text": "x@example.org"}, "source_ids": [],
    })
    assert r.status_code == 422


def test_api_proposal_lifecycle(client):
    r = client.post("/registry/proposals/predicates", json={
        "predicate_id": "api_test_pred", "label": "API Test",
        "range_kind": "string", "domain_type": "Person",
        "cardinality": "1:n", "proposed_by": "pytest-api",
    })
    assert r.status_code == 201
    proposal_id = r.json()["id"]

    r = client.post(f"/registry/proposals/predicates/{proposal_id}/approve")
    assert r.status_code == 200
    preds = {p["id"] for p in client.get("/registry/predicates").json()}
    assert "api_test_pred" in preds

    # Doppelt approven → 409
    r = client.post(f"/registry/proposals/predicates/{proposal_id}/approve")
    assert r.status_code == 409


def test_api_ingest_search_traverse(client):
    r = client.post("/ingest", json={
        "activity": "apify:linkedin", "agent": "pytest-api",
        "raw": {
            "kind": "social_profile", "name": "API Ingest Person",
            "email": "api-ingest@example.org",
            "employer": {"name": "API Ingest Org", "role": "Dev"},
        },
    })
    assert r.status_code == 201
    report = r.json()["pipeline"]
    assert report["committed"]

    hits = client.get("/search", params={
        "q": "API Ingest Person", "type_id": "Person",
    }).json()
    person_id = hits[0]["id"]

    paths = client.post("/query/traverse", json={
        "start_id": person_id, "max_depth": 2,
    }).json()
    assert any(p["label"] == "API Ingest Org" for p in paths)
