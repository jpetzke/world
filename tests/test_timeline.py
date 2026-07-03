"""Occurrent-Vertikale: Zeitleiste (Ereignisse + abgeleitete Meilensteine),
abstrakter Typ-Guard, Domain/Range der Ereignis-Prädikate."""

import pytest

from weltmodell.entities import create_entity
from weltmodell.errors import ValidationError
from weltmodell.queries import entity_timeline
from weltmodell.resolution import merge_entity
from weltmodell.statements import commit_statement, deprecate_statement


def _entity(conn, type_id, label):
    return str(create_entity(conn, type_id=type_id, label=label)["id"])


def _stmt(conn, subject, predicate, value, source_id):
    return commit_statement(
        conn, subject_id=subject, predicate_id=predicate,
        value=value, source_ids=[source_id],
    )


def test_timeline_event_and_milestones(conn, source_id):
    account = _entity(conn, "SocialMediaAccount", "@zeitleiste")
    sperrung = _entity(conn, "Kontosperrung", "Sperrung @zeitleiste 2025")

    _stmt(conn, sperrung, "betroffenes_konto",
          {"type": "entity", "object_id": account}, source_id)
    _stmt(conn, sperrung, "beginn",
          {"type": "datetime", "datetime": "2025-06-14T12:00:00Z"}, source_id)
    _stmt(conn, account, "erstellt_am",
          {"type": "datetime", "datetime": "2024-01-03T00:00:00Z"}, source_id)

    items = entity_timeline(conn, account)
    kinds = [(i["kind"], i.get("predicate_id") or i.get("type_id")) for i in items]

    # chronologisch: erstellt_am (2024) vor Sperrung (2025)
    assert kinds == [
        ("meilenstein", "erstellt_am"),
        ("ereignis", "Kontosperrung"),
    ]
    ereignis = items[1]
    assert ereignis["entity_id"] == sperrung
    assert ereignis["via"] == ["betroffenes_konto"]
    assert ereignis["beginn"] is not None


def test_timeline_handle_change_exactly_one_milestone(conn, source_id):
    """Supersession legt eine offene deprecated-Kopie an — der Wechsel darf
    trotzdem nur EINMAL erscheinen (Regression gegen Doppelzählung)."""
    account = _entity(conn, "SocialMediaAccount", "@alt")
    old = _stmt(conn, account, "handle", {"type": "string", "text": "@alt"}, source_id)
    deprecate_statement(conn, str(old["id"]))
    _stmt(conn, account, "handle", {"type": "string", "text": "@neu"}, source_id)

    changes = [i for i in entity_timeline(conn, account)
               if i["kind"] == "meilenstein" and i["predicate_id"] == "handle"]
    assert len(changes) == 1
    assert changes[0]["detail"] == "@alt → @neu"


def test_timeline_excludes_merged_events(conn, source_id):
    person = _entity(conn, "Person", "Merge-Teilnehmerin")
    e1 = _entity(conn, "Demonstration", "Demo A")
    e2 = _entity(conn, "Demonstration", "Demo A (Duplikat)")
    _stmt(conn, e1, "teilnehmer", {"type": "entity", "object_id": person}, source_id)
    _stmt(conn, e2, "teilnehmer", {"type": "entity", "object_id": person}, source_id)

    merge_entity(conn, e2, e1)

    events = [i for i in entity_timeline(conn, person) if i["kind"] == "ereignis"]
    assert [e["entity_id"] for e in events] == [e1]


def test_abstract_types_not_creatable(client):
    for type_id in ("Ereignis", "Agent"):
        r = client.post("/api/entities", json={"type_id": type_id, "label": "x"})
        assert r.status_code == 422, type_id


def test_event_predicate_domain_range(conn, source_id):
    wahl = _entity(conn, "Wahl", "Testwahl 2029")
    person = _entity(conn, "Person", "Kandidatin K")
    ort = _entity(conn, "Ort", "Teststadt")

    # kandidat → Agent-Subtyp Person: ok
    _stmt(conn, wahl, "kandidat", {"type": "entity", "object_id": person}, source_id)
    # ort auf der Ereignis-Wurzel, geerbt von Wahl: ok
    _stmt(conn, wahl, "ort", {"type": "entity", "object_id": ort}, source_id)

    # ort → Person: falscher Range-Typ
    with pytest.raises(ValidationError):
        _stmt(conn, wahl, "ort", {"type": "entity", "object_id": person}, source_id)
    # beginn auf einem Continuant: falsche Domain
    with pytest.raises(ValidationError):
        _stmt(conn, person, "beginn",
              {"type": "datetime", "datetime": "2029-01-01T00:00:00Z"}, source_id)
