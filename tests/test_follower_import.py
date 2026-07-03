"""Follower-Listen-Import: Preview-Klassifikation, Commit, Re-Bestätigung."""

import pytest

from weltmodell.entities import create_entity
from weltmodell.errors import ValidationError
from weltmodell.follower_import import commit_follower_list, preview_follower_list
from weltmodell.resolution import get_or_create_entity


@pytest.fixture
def owner(conn, source_id):
    entity_id, _ = get_or_create_entity(
        conn, type_id="SocialMediaAccount", label="@jonas_ptzk",
        identifiers={"account_uri": "instagram:jonas_ptzk"}, source_ids=[source_id],
    )
    return entity_id


def _active_follows(conn, subject_id, object_id):
    return conn.execute(
        """SELECT * FROM statement
           WHERE predicate_id = 'follows' AND subject_id = %s AND object_id = %s
             AND system_to IS NULL AND rank <> 'deprecated'""",
        (subject_id, object_id),
    ).fetchall()


def test_preview_classifies_rows(conn, owner, source_id):
    existing_id, _ = get_or_create_entity(
        conn, type_id="SocialMediaAccount", label="@katharina.bzl",
        identifiers={"account_uri": "instagram:katharina.bzl"}, source_ids=[source_id],
    )
    result = preview_follower_list(
        conn, owner_entity_id=owner, direction="followers",
        rows=[
            {"username": "katharina.bzl", "display_name": "Katharina"},
            {"username": "Leopold.st2729", "display_name": None},  # neu, Case-Fix
            {"username": "jonas_ptzk", "display_name": None},      # Owner selbst
            {"username": "NICHT GÜLTIG!", "display_name": None},   # invalid
            {"username": "katharina.bzl", "display_name": None},   # In-Batch-Duplikat
        ],
    )
    by_name = {r["username"]: r for r in result["rows"]}
    assert by_name["katharina.bzl"]["status"] == "new_follow"
    assert by_name["katharina.bzl"]["entity_id"] == existing_id
    assert by_name["leopold.st2729"]["status"] == "new_account"
    assert by_name["jonas_ptzk"]["status"] == "invalid"
    assert result["summary"] == {"total": 4, "new_account": 1, "new_follow": 1,
                                 "confirmed": 0, "invalid": 2}


def test_preview_rejects_non_account_owner(conn, source_id):
    person = create_entity(conn, type_id="Person", label="Jonas")
    with pytest.raises(ValidationError, match="SocialMediaAccount"):
        preview_follower_list(
            conn, owner_entity_id=str(person["id"]), direction="followers",
            rows=[{"username": "abc", "display_name": None}],
        )


def test_commit_creates_accounts_and_follows(conn, owner):
    result = commit_follower_list(
        conn, owner_entity_id=owner, direction="followers",
        observed_at="2026-07-03T12:00:00+00:00",
        rows=[
            {"username": "carina_sch03", "display_name": "Carina"},
            {"username": "lui1se", "display_name": None},
        ],
    )
    assert result["accounts_created"] == 2
    assert result["follows_created"] == 2
    assert result["follows_confirmed"] == 0

    # Account trägt handle, platform→Instagram, Display-Name als 'name'
    row = conn.execute(
        """SELECT e.id, e.label FROM statement s JOIN entity e ON e.id = s.subject_id
           WHERE s.predicate_id = 'account_uri'
             AND s.value_text = 'instagram:carina_sch03'""",
    ).fetchone()
    # Label-Cache spiegelt den handle (label_predicate) — Convention ohne '@'
    assert row["label"] == "carina_sch03"
    stmts = conn.execute(
        "SELECT predicate_id, value_text FROM statement WHERE subject_id = %s",
        (row["id"],),
    ).fetchall()
    by_pred = {s["predicate_id"]: s for s in stmts}
    assert by_pred["handle"]["value_text"] == "carina_sch03"
    assert by_pred["name"]["value_text"] == "Carina"
    assert "platform" in by_pred

    # Richtung followers: Row-Account folgt Owner
    follows = _active_follows(conn, row["id"], owner)
    assert len(follows) == 1
    assert str(follows[0]["valid_from"].date()) == "2026-07-03"


def test_commit_direction_following(conn, owner):
    commit_follower_list(
        conn, owner_entity_id=owner, direction="following",
        rows=[{"username": "petologie", "display_name": "peter"}],
    )
    row = conn.execute(
        """SELECT e.id FROM statement s JOIN entity e ON e.id = s.subject_id
           WHERE s.predicate_id = 'account_uri'
             AND s.value_text = 'instagram:petologie'""",
    ).fetchone()
    assert len(_active_follows(conn, owner, row["id"])) == 1
    assert len(_active_follows(conn, row["id"], owner)) == 0


def test_reimport_confirms_instead_of_duplicating(conn, owner):
    rows = [{"username": "natalie.slr", "display_name": "natalie"}]
    first = commit_follower_list(
        conn, owner_entity_id=owner, direction="followers", rows=rows,
    )
    assert first["follows_created"] == 1

    second = commit_follower_list(
        conn, owner_entity_id=owner, direction="followers", rows=rows,
    )
    assert second["accounts_created"] == 0
    assert second["follows_created"] == 0
    assert second["follows_confirmed"] == 1

    # Kein Duplikat-Statement, aber zweite Quelle als reference angehängt
    account = conn.execute(
        """SELECT e.id FROM statement s JOIN entity e ON e.id = s.subject_id
           WHERE s.predicate_id = 'account_uri'
             AND s.value_text = 'instagram:natalie.slr'""",
    ).fetchone()
    follows = _active_follows(conn, account["id"], owner)
    assert len(follows) == 1
    refs = conn.execute(
        "SELECT source_id FROM reference WHERE statement_id = %s",
        (follows[0]["id"],),
    ).fetchall()
    assert {str(r["source_id"]) for r in refs} == {first["source_id"],
                                                   second["source_id"]}

    # Preview meldet die Row jetzt als bestätigt
    preview = preview_follower_list(
        conn, owner_entity_id=owner, direction="followers", rows=rows,
    )
    assert preview["rows"][0]["status"] == "confirmed"


def test_commit_skips_invalid_rows(conn, owner):
    result = commit_follower_list(
        conn, owner_entity_id=owner, direction="followers",
        rows=[{"username": "so nicht", "display_name": None},
              {"username": "gueltig.acc", "display_name": None}],
    )
    assert result["skipped_invalid"] == 1
    assert result["follows_created"] == 1


def test_api_roundtrip(client, owner, conn):
    conn.commit()  # Fixture-Daten für die API-Session sichtbar machen
    preview = client.post("/api/ingest/follower-list/preview", json={
        "owner_entity_id": owner, "direction": "followers",
        "rows": [{"username": "rich.vda", "display_name": "Richard"}],
    })
    assert preview.status_code == 200
    assert preview.json()["summary"]["new_account"] == 1

    commit = client.post("/api/ingest/follower-list/commit", json={
        "owner_entity_id": owner, "direction": "followers",
        "observed_at": "2026-07-03T12:00:00+00:00",
        "rows": [{"username": "rich.vda", "display_name": "Richard"}],
    })
    assert commit.status_code == 201
    body = commit.json()
    assert body["accounts_created"] == 1
    assert body["follows_created"] == 1
