"""Smoke-Suite (Deploy-Gate): jedes MCP-Tool mindestens einmal erfolgreich.

Hintergrund: Beim letzten Update teilten sich drei Tools einen Session-Handling-
Bug, der bei JEDEM Aufruf crashte. Genau diese Regression-Klasse fängt diese
Suite ab: ein erfolgreicher End-to-End-Aufruf pro Tool gegen die ephemere
Test-DB (conftest: frisches Schema + Registry-Seed; die Verfassung kommt aus
src/weltmodell/constitution.md — kein Zugriff auf Produktionsdaten).

Vollständigkeits-Guard: der letzte Test vergleicht tools/list mit den
tatsächlich aufgerufenen Tools — ein neues Tool ohne Smoke-Aufruf bricht die
Suite. Ausführen: uv run pytest -m smoke (vor jedem Deploy, siehe README).
"""

import pytest
from test_mcp import _dance, _mcp_call, _tool

pytestmark = pytest.mark.smoke

_called: set[str] = set()
S: dict[str, str] = {}  # geteilte IDs entlang des Szenarios (Dateireihenfolge)


class SmokeCaller:
    def __init__(self, client, token: str):
        self.client = client
        self.token = token

    def __call__(self, name: str, arguments: dict | None = None) -> dict:
        result = _tool(self.client, self.token, name, arguments)
        assert result.get("isError") is not True, f"{name}: {result}"
        _called.add(name)
        return result


@pytest.fixture(scope="module")
def smoke(client):
    from weltmodell import auth as auth_module

    auth_module._failures.clear()
    _, _, _, tokens = _dance(client)
    caller = SmokeCaller(client, tokens["access_token"])
    caller("welt_constitution")  # öffnet das Verfassungs-Gate für Schreib-Tools
    return caller


def test_discovery_tools(smoke):
    stats = smoke("welt_stats")["structuredContent"]
    assert "entities" in stats
    vocab = smoke("welt_vocabulary")["structuredContent"]
    assert any(t["id"] == "Person" for t in vocab["types"])
    smoke("welt_proposals")
    smoke("welt_sources")
    smoke("welt_entities")


def test_source_and_entity_creation(smoke):
    src = smoke("welt_create_source", {
        "activity": "test:smoke", "agent": "pytest-smoke",
        "url": "https://example.org/smoke",
    })["structuredContent"]
    S["source_id"] = src["id"]

    person = smoke("welt_create_entity", {
        "type_id": "Person", "label": "Smoke Person",
    })["structuredContent"]
    S["person_id"] = person["id"]

    bulk = smoke("welt_create_entities", {"entities": [
        {"type_id": "Person", "label": "Smoke Dupe"},
        {"type_id": "SocialMediaAccount", "label": "smokeacct"},
    ]})["structuredContent"]
    assert bulk["committed"] == 2
    S["dupe_id"] = bulk["results"][0]["id"]
    S["account_id"] = bulk["results"][1]["id"]


def test_statement_lifecycle(smoke):
    stmt = smoke("welt_commit_statement", {
        "subject_id": S["person_id"], "predicate_id": "name",
        "value": {"type": "string", "text": "Smoke Person"},
        "source_ids": [S["source_id"]], "confidence": 0.9,
    })["structuredContent"]

    bulk = smoke("welt_commit_statements", {"statements_batch": [
        {"subject_id": S["person_id"], "predicate_id": "alias",
         "value": {"type": "string", "text": "Smokey"},
         "source_ids": [S["source_id"]]},
        {"subject_id": S["person_id"], "predicate_id": "owns_account",
         "value": {"type": "entity", "object_id": S["account_id"]},
         "source_ids": [S["source_id"]]},
    ]})["structuredContent"]
    assert bulk["committed"] == 2
    alias_id = bulk["results"][0]["id"]

    r = smoke("welt_set_rank", {"statement_id": stmt["id"], "rank": "preferred"})
    assert r["structuredContent"]["rank"] == "preferred"

    fixed = smoke("welt_fix_statement", {
        "statement_id": alias_id, "reason": "Smoke-Erratum", "confidence": 0.8,
    })["structuredContent"]
    assert fixed["id"] == alias_id

    dep = smoke("welt_deprecate_statement", {"statement_id": alias_id})
    assert dep["structuredContent"]["rank"] == "deprecated"


def test_read_views(smoke):
    view = smoke("welt_entity", {"entity_id": S["person_id"]})["structuredContent"]
    assert view["entity"]["id"] == S["person_id"]
    smoke("welt_timeline", {"entity_id": S["person_id"]})
    graph = smoke("welt_traverse", {
        "start_id": S["person_id"], "max_depth": 1,
    })["structuredContent"]
    assert S["person_id"] in {n["id"] for n in graph["nodes"]}
    smoke("welt_search", {"q": "Smoke"})
    smoke("welt_resolve", {"type_id": "Person", "label": "Smoke Person"})
    smoke("welt_source", {"source_id": S["source_id"]})


def test_query(smoke):
    res = smoke("welt_query", {
        "predicate_id": "name", "value_text": "Smoke Person",
    })["structuredContent"]
    assert res["total"] >= 1
    agg = smoke("welt_query", {
        "predicate_id": "name", "aggregate": "count",
    })["structuredContent"]
    assert agg["count"] >= 1


def test_analyse_tools(smoke):
    m = smoke("welt_match", {
        "patterns": [{"s": "?p", "p": "owns_account", "o": S["account_id"]}],
        "select": ["?p"],
    })["structuredContent"]
    assert m["total"] >= 1

    s = smoke("welt_set", {
        "operation": "union", "on": "subject",
        "queries": [{"predicate_id": "owns_account"}, {"predicate_id": "knows"}],
    })["structuredContent"]
    assert s["total"] >= 1

    p = smoke("welt_path", {
        "start_id": S["person_id"], "end_id": S["account_id"],
    })["structuredContent"]
    assert p["paths"] and p["path_length"] == 1

    c = smoke("welt_common", {
        "entity_ids": [S["person_id"], S["account_id"]], "min_shared": 1,
    })["structuredContent"]
    assert "neighbors" in c and "total" in c

    r = smoke("welt_rank", {"metric": "degree", "top": 200})["structuredContent"]
    assert any(i["id"] == S["person_id"] for i in r["items"])

    cl = smoke("welt_cluster", {"min_size": 1})["structuredContent"]
    assert cl["total"] >= 1

    sim = smoke("welt_similar", {"entity_id": S["person_id"]})["structuredContent"]
    assert "items" in sim

    ch = smoke("welt_changes", {
        "since": "2000-01-01T00:00:00+00:00",
    })["structuredContent"]
    assert ch["total"] >= 1

    sql = smoke("welt_sql", {
        "query": "SELECT count(*) AS n FROM v_statements",
    })["structuredContent"]
    assert sql["rows"][0]["n"] >= 1


def test_fix_entity(smoke):
    e = smoke("welt_create_entity", {
        "type_id": "Person", "label": "Smoke Wegwerf",
    })["structuredContent"]
    r = smoke("welt_fix_entity", {
        "entity_id": e["id"], "reason": "Smoke-Erratum",
    })["structuredContent"]
    assert r["deleted"] is True


def test_merge(smoke):
    r = smoke("welt_merge_entities", {
        "entity_id": S["dupe_id"], "target_id": S["person_id"],
    })["structuredContent"]
    assert r["into"] == S["person_id"]


def test_registry_gate(smoke):
    prop_t = smoke("welt_propose_type", {
        "type_id": "SmokeTyp", "parent_id": "Person", "kind": "continuant",
        "label": "Smoke-Typ", "rationale": "Smoke-Test",
    })["structuredContent"]
    approved = smoke("welt_decide_proposal", {
        "kind": "type", "proposal_id": prop_t["id"], "decision": "approve",
    })["structuredContent"]
    assert approved["status"] == "approved"

    prop_p = smoke("welt_propose_predicate", {
        "predicate_id": "smoke_praedikat", "label": "Smoke",
        "range_kind": "string", "domain_type": "Person", "cardinality": "1:n",
    })["structuredContent"]
    rejected = smoke("welt_decide_proposal", {
        "kind": "predicate", "proposal_id": prop_p["id"], "decision": "reject",
    })["structuredContent"]
    assert rejected["status"] == "rejected"


def test_proposal_komfort(smoke):
    iface = smoke("welt_propose_interface", {
        "interface_id": "SmokeFähig", "label": "Smoke-Fähigkeit",
    })["structuredContent"]
    approved = smoke("welt_decide_proposal", {
        "kind": "interface", "proposal_id": iface["id"], "decision": "approve",
    })["structuredContent"]
    assert approved["status"] == "approved"

    bulk_t = smoke("welt_propose_types", {"proposals": [
        {"type_id": "SmokeBulkTyp", "parent_id": "Person",
         "kind": "continuant", "label": "Smoke Bulk"},
    ]})["structuredContent"]
    assert bulk_t["committed"] == 1

    bulk_p = smoke("welt_propose_predicates", {"proposals": [
        {"predicate_id": "smoke_bulk_pred", "label": "x",
         "range_kind": "string", "domain_type": "Person", "cardinality": "1:n"},
    ]})["structuredContent"]
    assert bulk_p["committed"] == 1

    amended = smoke("welt_amend_proposal", {
        "proposal_id": bulk_p["results"][0]["id"],
        "patch": {"label": "Smoke Bulk Prädikat"},
    })["structuredContent"]
    assert amended["label"] == "Smoke Bulk Prädikat"
    assert amended["status"] == "pending"


def test_snapshot_import(smoke):
    owner = smoke("welt_create_entity", {
        "type_id": "Person", "label": "Snapshot Owner",
    })["structuredContent"]
    rows = [{"label": "Snapshot Ziel",
             "identifiers": {"email": "snapshot.ziel@example.org"}}]
    prev = smoke("welt_import_snapshot", {
        "predicate_id": "knows", "owner_entity_id": owner["id"],
        "rows": rows, "mode": "preview",
    })["structuredContent"]
    assert prev["summary"]["new_entity"] == 1
    com = smoke("welt_import_snapshot", {
        "predicate_id": "knows", "owner_entity_id": owner["id"],
        "rows": rows, "mode": "commit",
    })["structuredContent"]
    assert com["statements_created"] == 1


def test_pipeline_ingest(smoke):
    r = smoke("welt_ingest", {
        "activity": "test:smoke", "agent": "pytest-smoke",
        "raw": {"kind": "social_profile", "name": "Smoke Profil",
                "email": "smoke@example.org"},
        "extractor": "rule-based",
    })["structuredContent"]
    assert r["pipeline"]["committed"]


def test_follower_import(smoke):
    preview = smoke("welt_import_follower_list", {
        "owner_entity_id": S["account_id"], "direction": "followers",
        "rows": [{"username": "smoke_follower"}], "mode": "preview",
    })["structuredContent"]
    assert preview["summary"]["total"] == 1

    commit = smoke("welt_import_follower_list", {
        "owner_entity_id": S["account_id"], "direction": "followers",
        "rows": [{"username": "smoke_follower"}], "mode": "commit",
    })["structuredContent"]
    assert commit["follows_created"] == 1


def test_alle_tools_abgedeckt(smoke):
    """Vollständigkeits-Guard: jedes annoncierte Tool wurde oben aufgerufen."""
    r = _mcp_call(smoke.client, smoke.token, "tools/list")
    assert r.status_code == 200, r.text
    advertised = {t["name"] for t in r.json()["result"]["tools"]}
    missing = advertised - _called
    assert not missing, (
        f"Tools ohne Smoke-Aufruf: {sorted(missing)} — für jedes neue Tool "
        "einen Smoke-Test in tests/test_smoke.py ergänzen."
    )
