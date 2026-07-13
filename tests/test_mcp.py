"""MCP-Server: OAuth-Flow (DCR → PKCE → Login → Token) + Tools + Verfassungs-Gate."""

import base64
import hashlib
import html
import json
import re
import secrets
from urllib.parse import parse_qs, urlparse

import pytest

REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture(scope="module")
def mcp_client(client):
    # Denselben TestClient wie die API-Tests nutzen: der MCP-Session-Manager
    # (App-Lifespan) lässt sich nur einmal pro Prozess starten.
    from weltmodell import auth as auth_module

    auth_module._failures.clear()
    return client


def _register(c, scope=None):
    payload = {
        "client_name": "pytest-probe",
        "redirect_uris": [REDIRECT_URI],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
    }
    if scope:
        payload["scope"] = scope
    r = c.post("/register", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _dance(c, scope=None):
    """Kompletter Flow: register → authorize → login → code → token."""
    client_id = _register(c, scope=scope)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode().rstrip("=")
    )
    params = {
        "client_id": client_id, "response_type": "code",
        "redirect_uri": REDIRECT_URI, "state": "state123",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    r = c.get("/authorize", params=params, follow_redirects=False)
    assert r.status_code in (302, 307), r.text
    login_url = r.headers["location"]
    assert "/oauth/login?txn=" in login_url
    txn = parse_qs(urlparse(login_url).query)["txn"][0]

    assert c.get(login_url).status_code == 200  # Formular rendert

    r = c.post("/oauth/login", data={
        "txn": txn, "username": "test", "password": "test",
    })
    assert r.status_code == 200, r.text
    # Erfolgsseite: 200 + Meta-Refresh (kein 302 nach Form-POST — CSP-Pitfall)
    m = re.search(r'content="0;url=([^"]+)"', r.text)
    assert m, r.text
    callback = html.unescape(m.group(1))
    q = parse_qs(urlparse(callback).query)
    assert q["state"][0] == "state123"
    code = q["code"][0]

    r = c.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT_URI, "client_id": client_id,
        "code_verifier": verifier,
    })
    assert r.status_code == 200, r.text
    return client_id, code, verifier, r.json()


def _mcp_call(c, token, method, params=None, headers=None):
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if headers:
        h.update(headers)
    return c.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {},
    }, headers=h)


def _tool(c, token, name, arguments=None):
    r = _mcp_call(c, token, "tools/call",
                  {"name": name, "arguments": arguments or {}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_metadata_endpoints(mcp_client):
    for path in (
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server/mcp",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        r = mcp_client.get(path)
        assert r.status_code == 200, f"{path}: {r.status_code}"
    meta = mcp_client.get("/.well-known/oauth-authorization-server").json()
    assert meta["authorization_endpoint"].endswith("/authorize")
    assert meta["token_endpoint"].endswith("/token")


def test_mcp_requires_token(mcp_client):
    r = mcp_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                      "method": "tools/list", "params": {}})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")


def test_trailing_slash_redirects_absolute(mcp_client):
    r = mcp_client.get("/mcp/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].startswith("http")
    assert r.headers["location"].endswith("/mcp")


def test_wrong_password_rerenders_form(mcp_client):
    client_id = _register(mcp_client)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(b"x" * 48).digest())
        .decode().rstrip("=")
    )
    r = mcp_client.get("/authorize", params={
        "client_id": client_id, "response_type": "code",
        "redirect_uri": REDIRECT_URI, "state": "s",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, follow_redirects=False)
    txn = parse_qs(urlparse(r.headers["location"]).query)["txn"][0]

    r = mcp_client.post("/oauth/login", data={
        "txn": txn, "username": "test", "password": "falsch",
    })
    assert r.status_code == 401
    assert "Falsche Zugangsdaten" in r.text

    # Txn überlebt den Fehlversuch — Retry mit korrektem Passwort klappt
    from weltmodell import auth as auth_module

    auth_module._failures.clear()
    r = mcp_client.post("/oauth/login", data={
        "txn": txn, "username": "test", "password": "test",
    })
    assert r.status_code == 200


def test_full_flow_tools_and_constitution_gate(mcp_client):
    _, _, _, tokens = _dance(mcp_client)
    at = tokens["access_token"]
    assert at.startswith("welt_at_")
    assert "welt:write" in tokens["scope"]

    # tools/list enthält Lese- und Schreib-Tools
    r = _mcp_call(mcp_client, at, "tools/list")
    assert r.status_code == 200, r.text
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"welt_constitution", "welt_search", "welt_commit_statement",
            "welt_propose_type", "welt_decide_proposal"} <= names

    # Schreib-Tool VOR Verfassungs-Lektüre → gesperrt
    result = _tool(mcp_client, at, "welt_create_entity",
                   {"type_id": "Person", "label": "Gate Test"})
    assert result.get("isError") is True
    assert "welt_constitution" in result["content"][0]["text"]

    # Verfassung lesen → Gate offen
    result = _tool(mcp_client, at, "welt_constitution")
    assert result.get("isError") is not True
    assert "Invarianten" in result["content"][0]["text"]

    result = _tool(mcp_client, at, "welt_create_entity",
                   {"type_id": "Person", "label": "MCP Person"})
    assert result.get("isError") is not True, result
    entity = result["structuredContent"]
    assert entity["type_id"] == "Person"

    # Lesen: Statistik + Vollsicht
    result = _tool(mcp_client, at, "welt_stats")
    assert result["structuredContent"]["entities"] >= 1

    result = _tool(mcp_client, at, "welt_entity", {"entity_id": entity["id"]})
    assert result["structuredContent"]["entity"]["label"] == "MCP Person"

    # Statement-Roundtrip mit Provenance
    result = _tool(mcp_client, at, "welt_create_source", {
        "activity": "test:mcp", "agent": "pytest-mcp",
        "url": "https://example.org/mcp",
    })
    source_id = result["structuredContent"]["id"]
    result = _tool(mcp_client, at, "welt_commit_statement", {
        "subject_id": entity["id"], "predicate_id": "name",
        "value": {"type": "string", "text": "MCP Person"},
        "source_ids": [source_id], "confidence": 0.9,
    })
    assert result.get("isError") is not True, result
    assert result["structuredContent"]["confidence"] == 0.9

    # Registry-Gate wirkt auch über MCP: unbekanntes Prädikat → sauberer Fehler
    result = _tool(mcp_client, at, "welt_commit_statement", {
        "subject_id": entity["id"], "predicate_id": "erfundenes_praedikat",
        "value": {"type": "string", "text": "x"}, "source_ids": [source_id],
    })
    assert result.get("isError") is True
    assert "erfundenes_praedikat" in result["content"][0]["text"]


def test_read_only_scope_blocks_writes(mcp_client):
    _, _, _, tokens = _dance(mcp_client, scope="welt:read")
    at = tokens["access_token"]

    result = _tool(mcp_client, at, "welt_constitution")
    assert result.get("isError") is not True

    result = _tool(mcp_client, at, "welt_create_entity",
                   {"type_id": "Person", "label": "Scope Test"})
    assert result.get("isError") is True
    assert "welt:write" in result["content"][0]["text"]


def test_prm_advertises_write_scope(mcp_client):
    # Regression: die Protected-Resource-Metadata muss welt:write als supported
    # annoncieren — sonst fragt claude.ai nur welt:read an und bekommt nie einen
    # schreibfähigen Token. required_scopes bleibt welt:read (Lesen unberührt).
    for path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        scopes = mcp_client.get(path).json()["scopes_supported"]
        assert "welt:read" in scopes and "welt:write" in scopes, f"{path}: {scopes}"


def test_client_adopting_advertised_scopes_can_write(mcp_client):
    # Realer claude.ai-Pfad: Client liest die PRM und fragt exakt die dort
    # annoncierten Scopes an. Mit dem Fix enthält das welt:write → Token kann
    # nach dem Verfassungs-Ack schreiben.
    scopes = mcp_client.get(
        "/.well-known/oauth-protected-resource").json()["scopes_supported"]
    _, _, _, tokens = _dance(mcp_client, scope=" ".join(scopes))
    assert "welt:write" in tokens["scope"]
    at = tokens["access_token"]
    _tool(mcp_client, at, "welt_constitution")  # Verfassungs-Gate öffnen
    result = _tool(mcp_client, at, "welt_create_entity",
                   {"type_id": "Person", "label": "Advertised Scope"})
    assert result.get("isError") is not True, result


def test_code_reuse_rejected(mcp_client):
    client_id, code, verifier, _ = _dance(mcp_client)
    r = mcp_client.post("/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT_URI, "client_id": client_id,
        "code_verifier": verifier,
    })
    assert r.status_code == 400


def test_refresh_rotation(mcp_client):
    client_id, _, _, tokens = _dance(mcp_client)
    rt = tokens["refresh_token"]

    r = mcp_client.post("/token", data={
        "grant_type": "refresh_token", "refresh_token": rt,
        "client_id": client_id,
    })
    assert r.status_code == 200, r.text
    fresh = r.json()
    assert fresh["access_token"] != tokens["access_token"]

    # Alter Refresh-Token ist rotiert → tot
    r = mcp_client.post("/token", data={
        "grant_type": "refresh_token", "refresh_token": rt,
        "client_id": client_id,
    })
    assert r.status_code == 400

    # Neuer Access-Token funktioniert
    r = _mcp_call(mcp_client, fresh["access_token"], "tools/list")
    assert r.status_code == 200


def test_host_forgery_rejected(mcp_client):
    _, _, _, tokens = _dance(mcp_client)
    r = _mcp_call(mcp_client, tokens["access_token"], "tools/list",
                  headers={"Host": "evil.example"})
    assert r.status_code == 421


def test_id_tools_no_longer_pass_id_into_conn_slot(mcp_client):
    # Regression: welt_set_rank / welt_traverse / welt_deprecate_statement banden
    # die id positional in partial → sie landete im conn-Slot → 'str' object has
    # no attribute 'execute'. Jetzt als kwarg gebunden.
    _, _, _, tokens = _dance(mcp_client)
    at = tokens["access_token"]
    _tool(mcp_client, at, "welt_constitution")  # Gate öffnen

    person = _tool(mcp_client, at, "welt_create_entity",
                   {"type_id": "Person", "label": "Traverse Person"})["structuredContent"]
    acc = _tool(mcp_client, at, "welt_create_entity",
                {"type_id": "SocialMediaAccount", "label": "@trav"})["structuredContent"]
    src = _tool(mcp_client, at, "welt_create_source",
                {"activity": "test:idtools", "agent": "pytest"})["structuredContent"]

    stmt = _tool(mcp_client, at, "welt_commit_statement", {
        "subject_id": person["id"], "predicate_id": "owns_account",
        "value": {"type": "entity", "object_id": acc["id"]},
        "source_ids": [src["id"]],
    })["structuredContent"]

    # welt_traverse — vorher kaputt
    r = _tool(mcp_client, at, "welt_traverse",
              {"start_id": person["id"], "max_depth": 1})
    assert r.get("isError") is not True, r
    node_ids = {n["id"] for n in r["structuredContent"]["nodes"]}
    assert person["id"] in node_ids and acc["id"] in node_ids

    # welt_set_rank — vorher kaputt (supersedet stmt)
    r = _tool(mcp_client, at, "welt_set_rank",
              {"statement_id": stmt["id"], "rank": "preferred"})
    assert r.get("isError") is not True, r
    assert r["structuredContent"]["rank"] == "preferred"

    # welt_deprecate_statement — vorher kaputt (frisches, aktuelles Statement)
    fresh = _tool(mcp_client, at, "welt_commit_statement", {
        "subject_id": person["id"], "predicate_id": "name",
        "value": {"type": "string", "text": "Traverse Person"},
        "source_ids": [src["id"]],
    })["structuredContent"]
    r = _tool(mcp_client, at, "welt_deprecate_statement",
              {"statement_id": fresh["id"]})
    assert r.get("isError") is not True, r
    assert r["structuredContent"]["rank"] == "deprecated"


def test_bulk_and_fix_tools_end_to_end(mcp_client):
    _, _, _, tokens = _dance(mcp_client)
    at = tokens["access_token"]
    _tool(mcp_client, at, "welt_constitution")  # Gate öffnen

    src = _tool(mcp_client, at, "welt_create_source",
                {"activity": "test:bulk", "agent": "pytest"})["structuredContent"]

    # Bulk-Entities
    ents = _tool(mcp_client, at, "welt_create_entities", {"entities": [
        {"type_id": "Person", "label": "Bulk One"},
        {"type_id": "Person", "label": "Bulk Two"},
    ]})["structuredContent"]
    assert ents["committed"] == 2
    pid = ents["results"][0]["id"]

    # Bulk-Statements, Best-Effort: ein gültiges + ein ungültiges Prädikat
    res = _tool(mcp_client, at, "welt_commit_statements", {
        "statements_batch": [
            {"subject_id": pid, "predicate_id": "name",
             "value": {"type": "string", "text": "Bulk One"}, "source_ids": [src["id"]]},
            {"subject_id": pid, "predicate_id": "nichtexistent",
             "value": {"type": "string", "text": "x"}, "source_ids": [src["id"]]},
        ], "atomic": False,
    })["structuredContent"]
    assert res["committed"] == 1
    assert res["results"][1]["ok"] is False
    good_id = res["results"][0]["id"]

    # fix: Wert in place überschreiben
    fixed = _tool(mcp_client, at, "welt_fix_statement", {
        "statement_id": good_id, "reason": "Korrektur",
        "value": {"type": "string", "text": "Bulk One Fixed"},
    })
    assert fixed.get("isError") is not True, fixed
    assert fixed["structuredContent"]["value_text"] == "Bulk One Fixed"

    # fix: reason fehlt → Reject
    r = _tool(mcp_client, at, "welt_fix_statement",
              {"statement_id": good_id, "reason": "  ", "rank": "preferred"})
    assert r.get("isError") is True
    assert "reason" in r["content"][0]["text"]

    # fix: delete
    d = _tool(mcp_client, at, "welt_fix_statement",
              {"statement_id": good_id, "reason": "weg", "delete": True})["structuredContent"]
    assert d["deleted"] is True


# --- Tool-Call-Log (mcp_tool_log) --------------------------------------------


def _log_rows():
    from weltmodell.db import get_conn

    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM mcp_tool_log ORDER BY id").fetchall()
    finally:
        conn.close()


def test_tool_call_logged(mcp_client):
    _, _, _, tokens = _dance(mcp_client)
    at = tokens["access_token"]
    before = len(_log_rows())

    _tool(mcp_client, at, "welt_stats")

    rows = _log_rows()
    assert len(rows) == before + 1
    row = rows[-1]
    assert row["tool"] == "welt_stats"
    assert row["status"] == "ok"
    assert row["error"] is None
    assert row["duration_ms"] >= 0
    assert row["result_bytes"] > 0
    assert row["token_hash"]
    assert row["args"] == {}


def test_tool_error_logged(mcp_client):
    _, _, _, tokens = _dance(mcp_client)
    at = tokens["access_token"]

    # Schreib-Tool ohne Verfassungs-Ack: Fehler erreicht Client UND Log
    result = _tool(mcp_client, at, "welt_create_entity",
                   {"type_id": "Person", "label": "Log Error Test"})
    assert result.get("isError") is True

    row = _log_rows()[-1]
    assert row["tool"] == "welt_create_entity"
    assert row["status"] == "error"
    assert "welt_constitution" in row["error"]
    assert row["args"]["label"] == "Log Error Test"


def test_tool_args_truncated(mcp_client):
    _, _, _, tokens = _dance(mcp_client)
    at = tokens["access_token"]

    q = "SELECT '" + "x" * 3000 + "' AS blob"
    result = _tool(mcp_client, at, "welt_sql", {"query": q})
    assert result.get("isError") is not True, result

    row = _log_rows()[-1]
    assert row["args"]["_truncated"] is True
    assert row["args"]["query"] == "…"
    assert len(json.dumps(row["args"])) < 2048


def test_welt_sql_reads_tool_log(mcp_client):
    _, _, _, tokens = _dance(mcp_client)
    at = tokens["access_token"]

    result = _tool(mcp_client, at, "welt_sql", {
        "query": "SELECT tool, status, count(*) AS n FROM v_tool_log "
                 "GROUP BY tool, status ORDER BY n DESC",
    })
    assert result.get("isError") is not True, result
    rows = result["structuredContent"]["rows"]
    assert any(r["tool"] == "welt_stats" for r in rows)


def test_healthz_access_log_filtered():
    import logging

    from weltmodell.api import _HealthzFilter

    def record(args):
        return logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 0,
            '%s - "%s %s HTTP/%s" %d', args, None,
        )

    f = _HealthzFilter()
    assert f.filter(record(("127.0.0.1:1", "GET", "/healthz", "1.1", 200))) is False
    assert f.filter(record(("127.0.0.1:1", "GET", "/api/stats", "1.1", 200))) is True
    assert f.filter(record(None)) is True  # unerwartetes Format nie verschlucken

    assert any(isinstance(fl, _HealthzFilter)
               for fl in logging.getLogger("uvicorn.access").filters)
