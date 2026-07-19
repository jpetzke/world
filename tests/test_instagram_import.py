"""Instagram-JSON-Ingest: Parsing, Owner-Anlage, Richtung, pk-Identität/Backfill,
leere/partielle Dateien, Idempotenz, Batch, API.

Alle Entity-Namen sind mit 'igx_' genamespacet: die Test-Suite teilt EINE
Session-DB (conftest.database ist session-scoped), Namen ohne Prefix kollidieren
sonst mit anderen Testdateien und verfälschen die exakten Zählungen.
"""

import json

import pytest

from weltmodell.errors import ValidationError
from weltmodell.instagram_import import (
    commit_file,
    commit_files,
    parse_file,
    preview_file,
    preview_files,
)
from weltmodell.resolution import get_or_create_entity


def _user(username, *, pk=None, full_name=None, is_verified=False, is_private=False):
    u = {"username": username, "is_verified": is_verified, "is_private": is_private}
    if pk is not None:
        u["pk"] = pk
    if full_name is not None:
        u["full_name"] = full_name
    return u


def _file(kind, handle, users, *, user_id=None, status="done",
          captured_at="2026-07-19T19:00:00", expected_total=None):
    return {
        "metadata": {
            "kind": kind, "handle": handle, "user_id": user_id, "status": status,
            "captured_at": captured_at,
            "expected_total": len(users) if expected_total is None else expected_total,
        },
        "users": users,
    }


def _account(conn, username):
    return conn.execute(
        """SELECT e.id FROM statement s JOIN entity e ON e.id = s.subject_id
           WHERE s.predicate_id = 'account_uri' AND s.value_text = %s
             AND s.system_to IS NULL""",
        (f"instagram:{username}",),
    ).fetchone()


def _stmts(conn, entity_id):
    rows = conn.execute(
        """SELECT predicate_id, value_text, value_number FROM statement
           WHERE subject_id = %s AND system_to IS NULL AND rank <> 'deprecated'""",
        (entity_id,),
    ).fetchall()
    return {r["predicate_id"]: r for r in rows}


def _active_follows(conn, subject_id, object_id):
    return conn.execute(
        """SELECT * FROM statement WHERE predicate_id = 'follows'
           AND subject_id = %s AND object_id = %s
           AND system_to IS NULL AND rank <> 'deprecated'""",
        (subject_id, object_id),
    ).fetchall()


# --- Parsing ----------------------------------------------------------------

def test_parse_derives_owner_direction_and_normalizes(conn):
    parsed = parse_file(_file("followers", "Igx_OwnerH", [
        _user("Igx_Alice01", pk=111, full_name="  Alice  ", is_verified=True),
        _user("nope nope"),               # invalid username
        _user("igx_alice01"),             # In-Batch-Duplikat (case)
    ], user_id="1000000"))
    assert parsed["kind"] == "followers"
    assert parsed["direction"] == "incoming"
    assert parsed["owner"] == {"username": "igx_ownerh", "pk": "1000000"}
    valid = [r for r in parsed["rows"] if r.get("status") != "invalid"]
    assert len(valid) == 1
    assert valid[0] == {"username": "igx_alice01", "full_name": "Alice", "pk": "111",
                        "is_verified": 1, "is_private": 0}
    assert sum(1 for r in parsed["rows"] if r.get("status") == "invalid") == 1


def test_parse_rejects_broken_file(conn):
    with pytest.raises(ValidationError, match="metadata"):
        parse_file({"users": []})
    with pytest.raises(ValidationError, match="kind"):
        parse_file(_file("freunde", "igx_o", []))
    with pytest.raises(ValidationError, match="handle"):
        parse_file(_file("followers", "NICHT GÜLTIG!", []))


# --- Commit -----------------------------------------------------------------

def test_commit_creates_owner_accounts_follows_attributes(conn):
    result = commit_file(conn, filename="owner_followers.json", data=_file(
        "followers", "igx_owner", [
            _user("igx_carina", pk="222", full_name="Carina", is_verified=True),
            _user("igx_lui", pk="333", is_private=True),
        ], user_id="999", expected_total=42))

    assert result["owner_created"] is True
    assert result["accounts_created"] == 3          # owner + 2 rows
    assert result["follows_created"] == 2
    assert result["follows_confirmed"] == 0

    owner = _account(conn, "igx_owner")
    carina = _account(conn, "igx_carina")
    by = _stmts(conn, carina["id"])
    assert by["handle"]["value_text"] == "igx_carina"
    assert by["instagram_pk"]["value_text"] == "222"
    assert by["name"]["value_text"] == "Carina"
    assert by["is_verified"]["value_number"] == 1
    assert by["is_private"]["value_number"] == 0
    assert "platform" in by

    # Richtung followers: Row-Account folgt Owner
    follows = _active_follows(conn, carina["id"], owner["id"])
    assert len(follows) == 1
    assert str(follows[0]["valid_from"].date()) == "2026-07-19"

    # Owner trägt die gemeldete Follower-Zahl mit valid_from=captured_at
    assert _stmts(conn, owner["id"])["follower_count"]["value_number"] == 42


def test_commit_direction_following(conn):
    commit_file(conn, filename="o_following.json",
                data=_file("following", "igx_owner2", [_user("igx_pet", pk="7")]))
    owner = _account(conn, "igx_owner2")
    pet = _account(conn, "igx_pet")
    assert len(_active_follows(conn, owner["id"], pet["id"])) == 1   # owner folgt
    assert len(_active_follows(conn, pet["id"], owner["id"])) == 0
    assert "following_count" in _stmts(conn, owner["id"])


def test_pk_backfilled_onto_legacy_account(conn, source_id):
    # Paste-Alt-Account: nur account_uri, keine pk.
    legacy, _ = get_or_create_entity(
        conn, type_id="SocialMediaAccount", label="igx_legacy",
        identifiers={"account_uri": "instagram:igx_legacy"}, source_ids=[source_id])
    result = commit_file(conn, filename="f.json",
                         data=_file("followers", "igx_ownerx", [_user("igx_legacy", pk="555")]))
    # igx_legacy wird gematcht (kein neuer Account), nur der Owner ist neu.
    assert result["accounts_created"] == 1
    hit = _account(conn, "igx_legacy")
    assert str(hit["id"]) == str(legacy)
    assert _stmts(conn, hit["id"])["instagram_pk"]["value_text"] == "555"


def test_fully_operational_without_pk(conn):
    data = _file("followers", "igx_ownerz", [_user("igx_bob"), _user("igx_carla")])
    first = commit_file(conn, filename="a.json", data=data)
    assert first["accounts_created"] == 3
    assert first["follows_created"] == 2
    bob = _account(conn, "igx_bob")
    assert "instagram_pk" not in _stmts(conn, bob["id"])
    # Re-Import derselben (pk-losen) Datei: re-bestätigt statt dupliziert.
    second = commit_file(conn, filename="a.json", data=data)
    assert second["accounts_created"] == 0
    assert second["follows_created"] == 0
    assert second["follows_confirmed"] == 2


def test_empty_file_still_logs_owner_and_count(conn):
    result = commit_file(conn, filename="rate_limited.json", data=_file(
        "followers", "igx_blocked", [], status="failed: FeedbackRequired",
        expected_total=221))
    assert result["owner_created"] is True
    assert result["accounts_created"] == 1     # nur Owner
    assert result["follows_created"] == 0
    assert result["rows_total"] == 0
    owner = _account(conn, "igx_blocked")
    assert _stmts(conn, owner["id"])["follower_count"]["value_number"] == 221


def test_reimport_is_idempotent(conn):
    data = _file("followers", "igx_idem", [_user("igx_xacc", pk="1"), _user("igx_yacc", pk="2")])
    commit_file(conn, filename="i.json", data=data)
    second = commit_file(conn, filename="i.json", data=data)
    assert second["accounts_created"] == 0
    assert second["follows_created"] == 0
    assert second["follows_confirmed"] == 2
    owner = _account(conn, "igx_idem")
    x = _account(conn, "igx_xacc")
    assert len(_active_follows(conn, x["id"], owner["id"])) == 1


# --- Preview ----------------------------------------------------------------

def _entity_count(conn):
    return conn.execute("SELECT count(*) AS n FROM entity").fetchone()["n"]


def test_preview_writes_nothing(conn):
    data = _file("followers", "igx_prev", [_user("igx_neu1", pk="9"), _user("igx_neu2")])
    before = _entity_count(conn)
    result = preview_file(conn, filename="p.json", data=data)
    assert _entity_count(conn) == before          # read-only
    assert result["owner_exists"] is False
    assert result["valid"] == 2
    assert result["accounts_new"] == 2
    assert result["follows_new"] == 2
    assert result["follows_confirmed"] == 0


def test_preview_reflects_existing_after_commit(conn):
    data = _file("followers", "igx_pc", [_user("igx_known", pk="12")])
    commit_file(conn, filename="c.json", data=data)
    result = preview_file(conn, filename="c.json", data=data)
    assert result["owner_exists"] is True
    assert result["accounts_existing"] == 1
    assert result["follows_confirmed"] == 1
    assert result["follows_new"] == 0


# --- Batch ------------------------------------------------------------------

def test_same_file_twice_in_one_batch(conn):
    # Zweimal dieselbe Datei in EINEM Commit-Aufruf (eine Transaktion): die
    # zweite sieht die Writes der ersten (read-your-writes) → dedup greift.
    f = _file("followers", "igx_dup", [_user("igx_dupfol", pk="4242")])
    res = commit_files(conn, [
        {"filename": "dup.json", "data": f},
        {"filename": "dup.json", "data": f},
    ])
    assert res["totals"]["accounts_created"] == 2      # owner + follower, EINMAL
    assert res["totals"]["follows_created"] == 1
    assert res["totals"]["follows_confirmed"] == 1     # zweite Datei re-bestätigt
    owner = _account(conn, "igx_dup")
    fol = _account(conn, "igx_dupfol")
    follows = _active_follows(conn, fol["id"], owner["id"])
    assert len(follows) == 1                            # kein Duplikat-Statement
    refs = conn.execute(
        "SELECT count(*) AS n FROM reference WHERE statement_id = %s", (follows[0]["id"],)
    ).fetchone()["n"]
    assert refs == 2                                    # zwei Quellen (Provenance)


def test_batch_isolates_broken_file(conn):
    items = [
        {"filename": "good.json", "data": _file("following", "igx_batch", [_user("igx_gg", pk="3")])},
        {"filename": "broken.json", "data": {"metadata": {"kind": "x"}}},
    ]
    prev = preview_files(conn, items)
    assert prev["totals"]["files"] == 2
    assert prev["totals"]["files_failed"] == 1
    assert any("error" in f for f in prev["files"])

    res = commit_files(conn, items)
    assert res["totals"]["files_failed"] == 1
    assert res["totals"]["follows_created"] == 1


# --- API --------------------------------------------------------------------

def test_api_roundtrip(client):
    payload = _file("followers", "igx_apiown", [_user("igx_apifol", pk="77", full_name="API")])
    files = [("files", ("igx_apiown_followers.json",
                        json.dumps(payload).encode(), "application/json"))]

    preview = client.post("/api/ingest/instagram/preview", files=files)
    assert preview.status_code == 200, preview.text
    assert preview.json()["totals"]["follows_new"] == 1

    commit = client.post("/api/ingest/instagram/commit", files=files)
    assert commit.status_code == 201, commit.text
    body = commit.json()
    assert body["totals"]["files"] == 1
    assert body["totals"]["follows_created"] == 1
    assert body["files"][0]["owner_handle"] == "igx_apiown"
