"""API-Keys: Verwaltung (Session-only) und Scope-Gate (read < write < admin)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def make_key(client):
    """Erzeugt Keys über die Session des eingeloggten conftest-Clients."""
    created: list[str] = []

    def _make(name: str, scope: str) -> dict:
        r = client.post("/api/keys", json={"name": name, "scope": scope})
        assert r.status_code == 201, r.text
        key = r.json()
        created.append(key["id"])
        return key

    yield _make
    for key_id in created:
        client.delete(f"/api/keys/{key_id}")


@pytest.fixture
def key_client(client):
    """Client OHNE Session, authentifiziert nur über Header."""
    from weltmodell.api import app

    def _make(secret: str, header: str = "bearer") -> TestClient:
        headers = (
            {"Authorization": f"Bearer {secret}"}
            if header == "bearer"
            else {"X-API-Key": secret}
        )
        return TestClient(app, headers=headers)

    return _make


# --- Verwaltung (Session-only) ----------------------------------------------


def test_key_management_requires_session(client, make_key, key_client):
    from weltmodell.api import app

    anon = TestClient(app)
    assert anon.get("/api/keys").status_code == 401
    # Auch ein admin-Key darf keine Keys verwalten — nur die Session.
    admin = make_key("admin-key", "admin")
    c = key_client(admin["secret"])
    assert c.get("/api/keys").status_code == 401
    assert c.post("/api/keys", json={"name": "x", "scope": "read"}).status_code == 401


def test_create_list_shows_secret(client, make_key):
    key = make_key("n8n-import", "write")
    assert key["secret"].startswith("wm_")
    assert key["scope"] == "write"
    listed = client.get("/api/keys").json()
    match = next(k for k in listed if k["id"] == key["id"])
    # Immer wieder anzeigbar — bewusste Produktentscheidung.
    assert match["secret"] == key["secret"]
    assert match["name"] == "n8n-import"


def test_create_rejects_bad_input(client):
    assert client.post("/api/keys", json={"name": "x", "scope": "root"}).status_code == 422
    assert client.post("/api/keys", json={"name": "   ", "scope": "read"}).status_code == 422


# --- Scope-Gate ---------------------------------------------------------------


def test_no_credentials_rejected(key_client):
    from weltmodell.api import app

    anon = TestClient(app)
    assert anon.get("/api/stats").status_code == 401


def test_invalid_key_rejected(key_client):
    c = key_client("wm_definitiv-nicht-vergeben")
    assert c.get("/api/stats").status_code == 401


def test_read_key_reads_but_never_writes(make_key, key_client):
    c = key_client(make_key("readonly", "read")["secret"])
    assert c.get("/api/stats").status_code == 200
    assert c.get("/api/registry/vocabulary").status_code == 200
    r = c.post("/api/entities", json={"type_id": "Person", "label": "Nope"})
    assert r.status_code == 403
    assert "read" in r.json()["detail"]


def test_x_api_key_header_works(make_key, key_client):
    c = key_client(make_key("header-variante", "read")["secret"], header="x-api-key")
    assert c.get("/api/stats").status_code == 200


def test_write_key_covers_read_and_write_not_admin(make_key, key_client):
    c = key_client(make_key("automation", "write")["secret"])
    assert c.get("/api/stats").status_code == 200  # Hierarchie: write ⊇ read
    r = c.post("/api/entities", json={"type_id": "Person", "label": "Key-Test Person"})
    assert r.status_code == 201, r.text
    proposal = {
        "type_id": "KeyTestTypeW", "parent_id": "Person", "kind": "continuant",
        "label": "Nope", "proposed_by": "pytest",
    }
    assert c.post("/api/registry/proposals/types", json=proposal).status_code == 403


def test_admin_key_can_do_everything_incl_gate(make_key, key_client):
    c = key_client(make_key("vollzugriff", "admin")["secret"])
    assert c.get("/api/stats").status_code == 200
    assert c.post(
        "/api/entities", json={"type_id": "Person", "label": "Key-Test Admin"}
    ).status_code == 201
    proposal = {
        "type_id": "KeyTestTypeA", "parent_id": "Person", "kind": "continuant",
        "label": "Key-Test-Typ", "rationale": "pytest", "proposed_by": "pytest",
    }
    r = c.post("/api/registry/proposals/types", json=proposal)
    assert r.status_code == 201, r.text
    proposal_id = r.json()["id"]
    r = c.post(f"/api/registry/proposals/types/{proposal_id}/approve", json={})
    assert r.status_code == 200, r.text


def test_rotate_invalidates_old_secret(make_key, key_client, client):
    key = make_key("rotierbar", "read")
    old_secret = key["secret"]
    r = client.post(f"/api/keys/{key['id']}/rotate")
    assert r.status_code == 200
    rotated = r.json()
    assert rotated["id"] == key["id"]
    assert rotated["scope"] == "read"
    assert rotated["secret"] != old_secret
    assert rotated["rotated_at"] is not None
    assert key_client(old_secret).get("/api/stats").status_code == 401
    assert key_client(rotated["secret"]).get("/api/stats").status_code == 200


def test_delete_invalidates_secret(make_key, key_client, client):
    key = make_key("wegwerf", "read")
    assert client.delete(f"/api/keys/{key['id']}").status_code == 204
    assert key_client(key["secret"]).get("/api/stats").status_code == 401
    assert key["id"] not in [k["id"] for k in client.get("/api/keys").json()]


def test_last_used_at_tracked(make_key, key_client, client):
    key = make_key("tracker", "read")
    assert key["last_used_at"] is None
    key_client(key["secret"]).get("/api/stats")
    listed = client.get("/api/keys").json()
    match = next(k for k in listed if k["id"] == key["id"])
    assert match["last_used_at"] is not None


def test_session_keeps_full_access(client):
    # Session (Login) bleibt Vollzugriff — kein Key nötig.
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/registry/proposals").status_code == 200
