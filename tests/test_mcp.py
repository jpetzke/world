"""MCP-Server: OAuth-Flow (DCR → PKCE → Login → Token) + Tools + Verfassungs-Gate."""

import base64
import hashlib
import html
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
