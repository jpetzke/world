"""Auth-Gate: Login, Session, Guard, Brute-Force-Lockout."""

import os

import pytest
from fastapi.testclient import TestClient

from weltmodell import auth


@pytest.fixture
def anon():
    """Frischer Client ohne Session; Lockout-State pro Test zurückgesetzt."""
    os.environ["AUTH_USERNAME"] = "test"
    os.environ["AUTH_PASSWORD"] = "test"
    auth._failures.clear()

    from weltmodell.api import app

    with TestClient(app) as c:
        yield c

    auth._failures.clear()


def test_protected_route_requires_session(anon):
    assert anon.get("/api/registry/types").status_code == 401


def test_me_unauthenticated(anon):
    assert anon.get("/api/auth/me").status_code == 401


def test_bad_credentials_rejected(anon):
    r = anon.post("/api/auth/login", json={"username": "test", "password": "wrong"})
    assert r.status_code == 401


def test_login_sets_secure_flags_and_grants_access(anon):
    r = anon.post("/api/auth/login", json={"username": "test", "password": "test"})
    assert r.status_code == 200
    cookie = r.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    # Session hält → Zugriff auf geschützte Route
    assert anon.get("/api/auth/me").status_code == 200


def test_logout_clears_session(anon):
    anon.post("/api/auth/login", json={"username": "test", "password": "test"})
    anon.post("/api/auth/logout")
    assert anon.get("/api/auth/me").status_code == 401


def test_brute_force_lockout(anon):
    for _ in range(5):
        assert anon.post("/api/auth/login", json={"username": "test", "password": "x"}).status_code == 401
    # 6. Versuch gesperrt — auch mit korrektem Passwort
    r = anon.post("/api/auth/login", json={"username": "test", "password": "test"})
    assert r.status_code == 429


def test_healthz_is_public(anon):
    assert anon.get("/healthz").status_code == 200
