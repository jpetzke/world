"""Instagram-Follower-Listen-Import: Preview (read-only) + Commit.

Rows kommen bereits frontend-geparst an ({username, display_name}).
Dedupe läuft deterministisch über den identifying key account_uri
('instagram:<username>'). Snapshot-Philosophie: Quellen sind unvollständig —
ein bereits bekanntes follows-Statement wird nicht dupliziert, sondern nur
mit einer reference auf die neue Quelle re-bestätigt (kein Unfollow-Tracking).
"""

import re
from typing import Any

import psycopg

from .entities import canonical_id, get_entity
from .errors import ValidationError
from .pipeline import ingest_document
from .registry import is_subtype
from .resolution import get_or_create_entity
from .statements import commit_statement

USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lowercase/strip, In-Batch-Dedupe, Validierung. Ungültige Rows werden
    markiert (status='invalid'), nicht verworfen — der Preview zeigt sie."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        username = (row.get("username") or "").strip().lstrip("@").lower()
        display_name = (row.get("display_name") or "").strip() or None
        if not USERNAME_RE.match(username):
            out.append({"username": username, "display_name": display_name,
                        "status": "invalid", "reason": "kein gültiger Instagram-Username"})
            continue
        if username in seen:
            continue  # In-Batch-Duplikat (z. B. Überlappung beim Kopieren)
        seen.add(username)
        out.append({"username": username, "display_name": display_name})
    return out


def _lookup_accounts(conn: psycopg.Connection, usernames: list[str]) -> dict[str, str]:
    """Batch-Variante von resolution.resolve Stufe 1: account_uri → entity_id."""
    if not usernames:
        return {}
    uris = [f"instagram:{u}" for u in usernames]
    rows = conn.execute(
        """SELECT s.value_text AS uri, e.id FROM statement s
           JOIN entity e ON e.id = s.subject_id
           WHERE s.predicate_id = 'account_uri' AND s.value_text = ANY(%s)
             AND s.system_to IS NULL AND s.rank <> 'deprecated'
             AND e.merged_into IS NULL""",
        (uris,),
    ).fetchall()
    return {r["uri"].removeprefix("instagram:"): str(r["id"]) for r in rows}


def _lookup_follows(
    conn: psycopg.Connection, owner_id: str, entity_ids: list[str], direction: str
) -> dict[str, str]:
    """Aktive follows-Statements zwischen Owner und Entities → entity_id → statement_id."""
    if not entity_ids:
        return {}
    if direction == "followers":
        sql = """SELECT s.subject_id AS eid, s.id FROM statement s
                 WHERE s.predicate_id = 'follows' AND s.object_id = %s
                   AND s.subject_id = ANY(%s::uuid[])
                   AND s.system_to IS NULL AND s.rank <> 'deprecated'"""
    else:
        sql = """SELECT s.object_id AS eid, s.id FROM statement s
                 WHERE s.predicate_id = 'follows' AND s.subject_id = %s
                   AND s.object_id = ANY(%s::uuid[])
                   AND s.system_to IS NULL AND s.rank <> 'deprecated'"""
    rows = conn.execute(sql, (owner_id, entity_ids)).fetchall()
    return {str(r["eid"]): str(r["id"]) for r in rows}


def _owner_username(conn: psycopg.Connection, owner_id: str) -> str | None:
    row = conn.execute(
        """SELECT value_text FROM statement
           WHERE subject_id = %s AND predicate_id = 'account_uri'
             AND system_to IS NULL AND rank <> 'deprecated'
           LIMIT 1""",
        (owner_id,),
    ).fetchone()
    if row and row["value_text"].startswith("instagram:"):
        return row["value_text"].removeprefix("instagram:")
    return None


def _check_owner(conn: psycopg.Connection, owner_entity_id: str) -> str:
    owner_id = canonical_id(conn, owner_entity_id)
    owner = get_entity(conn, owner_id)
    if not is_subtype(conn, owner["type_id"], "SocialMediaAccount"):
        raise ValidationError(
            f"Owner muss ein SocialMediaAccount sein, ist '{owner['type_id']}'"
        )
    return owner_id


def _classify(
    conn: psycopg.Connection,
    rows: list[dict[str, Any]],
    owner_id: str,
    direction: str,
) -> list[dict[str, Any]]:
    """Setzt pro Row status: invalid | new_account | new_follow | confirmed
    (+ entity_id/statement_id, wo bekannt)."""
    owner_name = _owner_username(conn, owner_id)
    valid = [r for r in rows if "status" not in r]
    accounts = _lookup_accounts(conn, [r["username"] for r in valid])
    follows = _lookup_follows(conn, owner_id, list(accounts.values()), direction)
    for row in valid:
        if row["username"] == owner_name:
            row.update(status="invalid", reason="Owner-Account selbst")
            continue
        entity_id = accounts.get(row["username"])
        if entity_id is None:
            row["status"] = "new_account"
        elif entity_id == owner_id:
            row.update(status="invalid", reason="Owner-Account selbst")
        elif entity_id in follows:
            row.update(status="confirmed", entity_id=entity_id,
                       statement_id=follows[entity_id])
        else:
            row.update(status="new_follow", entity_id=entity_id)
    return rows


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(rows), "new_account": 0, "new_follow": 0,
              "confirmed": 0, "invalid": 0}
    for row in rows:
        counts[row["status"]] += 1
    return counts


def preview_follower_list(
    conn: psycopg.Connection,
    *,
    rows: list[dict[str, Any]],
    owner_entity_id: str,
    direction: str,
) -> dict[str, Any]:
    """Read-only: klassifiziert jede Row gegen den Bestand. Schreibt nichts."""
    owner_id = _check_owner(conn, owner_entity_id)
    classified = _classify(conn, _normalize_rows(rows), owner_id, direction)
    return {"rows": classified, "summary": _summary(classified)}


def _instagram_platform_id(conn: psycopg.Connection) -> str:
    row = conn.execute(
        """SELECT id FROM entity WHERE type_id = 'Platform' AND label = 'Instagram'
           AND merged_into IS NULL"""
    ).fetchone()
    if row is None:
        raise ValidationError("Platform-Entity 'Instagram' fehlt (Seed 0004)")
    return str(row["id"])


def commit_follower_list(
    conn: psycopg.Connection,
    *,
    rows: list[dict[str, Any]],
    owner_entity_id: str,
    direction: str,
    observed_at: Any = None,
    agent: str = "ui:follower-import",
) -> dict[str, Any]:
    """Eine Quelle für den ganzen Batch; pro Row Account (get-or-create) +
    follows-Statement bzw. Re-Bestätigung per reference auf die neue Quelle."""
    owner_id = _check_owner(conn, owner_entity_id)
    normalized = _normalize_rows(rows)

    doc = ingest_document(
        conn,
        raw={"kind": "follower_list", "owner_entity_id": owner_id,
             "direction": direction,
             "observed_at": str(observed_at) if observed_at else None,
             "rows": [{"username": r["username"], "display_name": r["display_name"]}
                      for r in normalized if "status" not in r]},
        activity="follower_list_import",
        agent=agent,
        retrieved_at=observed_at,
    )
    sid = str(doc["id"])

    # Frisch klassifizieren (Preview-Daten könnten stale sein).
    classified = _classify(conn, normalized, owner_id, direction)
    platform_id = _instagram_platform_id(conn)

    counts = {"accounts_created": 0, "follows_created": 0,
              "follows_confirmed": 0, "skipped_invalid": 0}
    for row in classified:
        if row["status"] == "invalid":
            counts["skipped_invalid"] += 1
            continue

        if row["status"] == "confirmed":
            conn.execute(
                """INSERT INTO reference (statement_id, source_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (row["statement_id"], sid),
            )
            counts["follows_confirmed"] += 1
            continue

        entity_id = row.get("entity_id")
        if entity_id is None:
            username = row["username"]
            entity_id, created = get_or_create_entity(
                conn, type_id="SocialMediaAccount", label=username,
                identifiers={"account_uri": f"instagram:{username}"},
                source_ids=[sid],
            )
            if created:
                counts["accounts_created"] += 1
                commit_statement(
                    conn, subject_id=entity_id, predicate_id="handle",
                    value={"type": "string", "text": username}, source_ids=[sid],
                )
                commit_statement(
                    conn, subject_id=entity_id, predicate_id="platform",
                    value={"type": "entity", "object_id": platform_id},
                    source_ids=[sid],
                )
                if row["display_name"]:
                    commit_statement(
                        conn, subject_id=entity_id, predicate_id="name",
                        value={"type": "string", "text": row["display_name"]},
                        source_ids=[sid],
                    )

        subject_id, object_id = (
            (entity_id, owner_id) if direction == "followers"
            else (owner_id, entity_id)
        )
        commit_statement(
            conn, subject_id=subject_id, predicate_id="follows",
            value={"type": "entity", "object_id": object_id},
            source_ids=[sid], valid_from=observed_at,
        )
        counts["follows_created"] += 1

    return {"source_id": sid, **counts}
