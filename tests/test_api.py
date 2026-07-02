"""API-Layer: die FastAPI-Actions sind der erzwungene Schreibweg (§7.1)."""


def _source(client):
    r = client.post("/api/sources", json={
        "activity": "test:api", "agent": "pytest-api",
        "url": "https://example.org/api",
    })
    assert r.status_code == 201
    return r.json()["id"]


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_entity_statement_roundtrip(client):
    source = _source(client)
    person = client.post("/api/entities", json={
        "type_id": "Person", "label": "API Person",
    }).json()
    account = client.post("/api/entities", json={
        "type_id": "SocialMediaAccount", "label": "@apiaccount",
    }).json()

    r = client.post("/api/statements", json={
        "subject_id": person["id"], "predicate_id": "owns_account",
        "value": {"type": "entity", "object_id": account["id"]},
        "source_ids": [source],
        "qualifiers": [{"predicate_id": "since",
                        "value": {"type": "datetime", "datetime": "2024-01-01"}}],
    })
    assert r.status_code == 201, r.text

    view = client.get(f"/api/entities/{person['id']}").json()
    owns = next(s for s in view["statements"] if s["predicate_id"] == "owns_account")
    assert owns["object_label"] == "@apiaccount"
    assert {q["predicate_id"] for q in owns["qualifiers"]} == {"since"}


def test_api_rejects_gate_violations(client):
    source = _source(client)
    person = client.post("/api/entities", json={
        "type_id": "Person", "label": "API Reject Person",
    }).json()

    # Unbekanntes Prädikat → 422
    r = client.post("/api/statements", json={
        "subject_id": person["id"], "predicate_id": "made_up_predicate",
        "value": {"type": "string", "text": "x"}, "source_ids": [source],
    })
    assert r.status_code == 422

    # Unbekannter Typ → 422
    r = client.post("/api/entities", json={"type_id": "MadeUpType", "label": "x"})
    assert r.status_code == 422

    # Statement ohne Quelle scheitert schon am Payload-Schema
    r = client.post("/api/statements", json={
        "subject_id": person["id"], "predicate_id": "email",
        "value": {"type": "string", "text": "x@example.org"}, "source_ids": [],
    })
    assert r.status_code == 422


def test_api_proposal_lifecycle(client):
    r = client.post("/api/registry/proposals/predicates", json={
        "predicate_id": "api_test_pred", "label": "API Test",
        "range_kind": "string", "domain_type": "Person",
        "cardinality": "1:n", "proposed_by": "pytest-api",
    })
    assert r.status_code == 201
    proposal_id = r.json()["id"]

    r = client.post(f"/api/registry/proposals/predicates/{proposal_id}/approve")
    assert r.status_code == 200
    preds = {p["id"] for p in client.get("/api/registry/predicates").json()}
    assert "api_test_pred" in preds

    # Doppelt approven → 409
    r = client.post(f"/api/registry/proposals/predicates/{proposal_id}/approve")
    assert r.status_code == 409


def test_api_ingest_search_traverse(client):
    r = client.post("/api/ingest", json={
        "activity": "apify:linkedin", "agent": "pytest-api",
        "raw": {
            "kind": "social_profile", "name": "API Ingest Person",
            "email": "api-ingest@example.org",
            "accounts": [{"platform": "linkedin", "handle": "apiingest",
                          "uri": "linkedin.com/in/apiingest"}],
        },
    })
    assert r.status_code == 201
    report = r.json()["pipeline"]
    assert report["committed"]

    hits = client.get("/api/search", params={
        "q": "API Ingest Person", "type_id": "Person",
    }).json()
    person_id = hits[0]["id"]

    paths = client.post("/api/query/traverse", json={
        "start_id": person_id, "max_depth": 2,
    }).json()
    assert any(p["label"] == "@apiingest" for p in paths)


def test_api_stats_entities_sources(client):
    stats = client.get("/api/stats").json()
    assert stats["entities"] > 0 and stats["statements"] > 0
    assert any(t["type_id"] == "Person" for t in stats["by_type"])

    entities = client.get("/api/entities", params={"type_id": "Person"}).json()
    assert entities["total"] > 0
    assert all("statement_count" in e for e in entities["items"])

    filtered = client.get("/api/entities", params={"q": "API Ingest"}).json()
    assert any(e["label"] == "API Ingest Person" for e in filtered["items"])

    sources = client.get("/api/sources").json()
    assert sources["total"] > 0
    detail = client.get(f"/api/sources/{sources['items'][0]['id']}").json()
    assert "raw" in detail["source"]


def test_api_graph_snapshot(client):
    graph = client.get("/api/graph").json()
    assert graph["total_nodes"] >= len(graph["nodes"]) > 0
    node_ids = {n["id"] for n in graph["nodes"]}
    assert all("degree" in n and "type_id" in n for n in graph["nodes"])
    for edge in graph["edges"]:
        assert edge["subject_id"] in node_ids
        assert edge["object_id"] in node_ids
        assert edge["rank"] != "deprecated"
    # owns_account-Kante aus dem Ingest-Test muss sichtbar sein
    assert any(e["predicate_id"] == "owns_account" for e in graph["edges"])
