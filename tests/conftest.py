import os

import psycopg
import pytest

ADMIN_DSN = "postgresql://weltmodell:weltmodell@localhost:5433/weltmodell"
TEST_DSN = "postgresql://weltmodell:weltmodell@localhost:5433/weltmodell_test"

os.environ["WELTMODELL_DSN"] = TEST_DSN  # vor jedem App-Import setzen

from weltmodell.db import get_conn, run_migrations  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS weltmodell_test WITH (FORCE)")
        admin.execute("CREATE DATABASE weltmodell_test")
    applied = run_migrations(TEST_DSN)
    assert applied, "Migrationen müssen auf frischer DB laufen"
    yield


@pytest.fixture
def conn(database):
    c = get_conn(TEST_DSN)
    yield c
    c.commit()
    c.close()


@pytest.fixture(scope="session")
def client(database):
    # Auth ist jetzt aktiv (require_auth auf allen /api-Routen). Die API-Tests
    # laufen durch den echten Login-Pfad — realistischer als ein Bypass.
    os.environ["AUTH_USERNAME"] = "test"
    os.environ["AUTH_PASSWORD"] = "test"

    from fastapi.testclient import TestClient

    from weltmodell.api import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "test", "password": "test"})
        assert r.status_code == 200, r.text
        yield c


@pytest.fixture
def source_id(conn):
    from weltmodell.pipeline import ingest_document

    doc = ingest_document(
        conn, raw={"test": True}, url="https://example.org/test",
        activity="test:fixture", agent="pytest",
    )
    return str(doc["id"])
