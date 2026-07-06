"""Generischer Snapshot-Import (Kern hinter welt_import_snapshot und dem
follower_list-Wrapper): Klassifikation, Commit, Re-Bestätigung, Guards."""

import pytest

from weltmodell.entities import create_entity
from weltmodell.errors import ValidationError
from weltmodell.snapshot_import import commit_snapshot, preview_snapshot


@pytest.fixture
def owner(conn):
    return str(create_entity(conn, type_id="Person", label="Snap Owner")["id"])


def test_snapshot_roundtrip_und_rebestaetigung(conn, owner):
    rows = [{
        "label": "Snap Bekannte",
        "identifiers": {"email": "snap.bekannte@example.org"},
        "statements": [{"predicate_id": "alias",
                        "value": {"type": "string", "text": "Snappy"}}],
    }]

    preview = preview_snapshot(
        conn, predicate_id="knows", owner_entity_id=owner, rows=rows,
        direction="outgoing",
    )
    assert preview["rows"][0]["status"] == "new_entity"
    assert preview["rows"][0]["type_id"] == "Person"  # Default = range_type
    assert preview["summary"]["new_entity"] == 1

    first = commit_snapshot(
        conn, predicate_id="knows", owner_entity_id=owner, rows=rows,
        direction="outgoing", observed_at="2026-07-01T00:00:00+00:00",
    )
    assert first["entities_created"] == 1
    assert first["statements_created"] == 1

    # Neuanlage-Extras (statements) wurden mitcommittet
    target = conn.execute(
        """SELECT e.id FROM statement s JOIN entity e ON e.id = s.subject_id
           WHERE s.predicate_id = 'email'
             AND s.value_text = 'snap.bekannte@example.org'""",
    ).fetchone()
    alias = conn.execute(
        """SELECT value_text FROM statement
           WHERE subject_id = %s AND predicate_id = 'alias'""",
        (target["id"],),
    ).fetchone()
    assert alias["value_text"] == "Snappy"

    # Re-Import: kein Duplikat, Re-Bestätigung per Reference (2. Quelle)
    second = commit_snapshot(
        conn, predicate_id="knows", owner_entity_id=owner, rows=rows,
        direction="outgoing",
    )
    assert second["entities_created"] == 0
    assert second["statements_created"] == 0
    assert second["statements_confirmed"] == 1

    follows = conn.execute(
        """SELECT id FROM statement
           WHERE predicate_id = 'knows' AND subject_id = %s AND object_id = %s
             AND system_to IS NULL AND rank <> 'deprecated'""",
        (owner, target["id"]),
    ).fetchall()
    assert len(follows) == 1
    refs = conn.execute(
        "SELECT source_id FROM reference WHERE statement_id = %s",
        (follows[0]["id"],),
    ).fetchall()
    assert {str(r["source_id"]) for r in refs} == {first["source_id"],
                                                   second["source_id"]}

    preview2 = preview_snapshot(
        conn, predicate_id="knows", owner_entity_id=owner, rows=rows,
        direction="outgoing",
    )
    assert preview2["rows"][0]["status"] == "confirmed"


def test_snapshot_direction_incoming(conn, owner):
    rows = [{"label": "Snap Fan", "identifiers": {"email": "snap.fan@example.org"}}]
    commit_snapshot(
        conn, predicate_id="knows", owner_entity_id=owner, rows=rows,
        direction="incoming",
    )
    fan = conn.execute(
        """SELECT e.id FROM statement s JOIN entity e ON e.id = s.subject_id
           WHERE s.predicate_id = 'email' AND s.value_text = 'snap.fan@example.org'""",
    ).fetchone()
    edge = conn.execute(
        """SELECT 1 FROM statement
           WHERE predicate_id = 'knows' AND subject_id = %s AND object_id = %s
             AND system_to IS NULL""",
        (fan["id"], owner),
    ).fetchone()
    assert edge is not None


def test_snapshot_rejects_non_nm_predicate(conn, owner):
    with pytest.raises(ValidationError, match="n:m"):
        preview_snapshot(
            conn, predicate_id="owns_account", owner_entity_id=owner,
            rows=[{"label": "x"}], direction="outgoing",
        )


def test_snapshot_owner_domain_check(conn):
    acc = str(create_entity(
        conn, type_id="SocialMediaAccount", label="@snapacc")["id"])
    # knows verlangt Person als Subjekt — Account-Owner outgoing ist Verstoß
    with pytest.raises(ValidationError, match="Person"):
        preview_snapshot(
            conn, predicate_id="knows", owner_entity_id=acc,
            rows=[{"label": "x"}], direction="outgoing",
        )
