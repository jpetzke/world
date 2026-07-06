"""Instagram-Follower-Listen-Import — dünner Wrapper um den generischen
Snapshot-Import (snapshot_import.py).

Rows kommen bereits frontend-geparst an ({username, display_name}). Hier lebt
nur das Instagram-Spezifische: Username-Normalisierung/Validierung, der
identifying Key account_uri ('instagram:<username>'), handle/platform/name-
Statements bei Neuanlage und die follower/following → incoming/outgoing-
Übersetzung. Preview/Commit-Muster und Snapshot-Semantik (Re-Bestätigung
statt Duplikat, kein Unfollow-Tracking) liegen im Kern.
"""

import re
from typing import Any

import psycopg

from .errors import ValidationError
from .snapshot_import import commit_snapshot, preview_snapshot

USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")

# Kern-Status → historische Follower-Import-Vokabeln (API-/UI-kompatibel)
_STATUS_MAP = {"new_entity": "new_account", "new_statement": "new_follow"}
# followers = die Row-Accounts folgen dem Owner (Owner ist Objekt)
_DIRECTION = {"followers": "incoming", "following": "outgoing"}


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


def _instagram_platform_id(conn: psycopg.Connection) -> str:
    row = conn.execute(
        """SELECT id FROM entity WHERE type_id = 'Platform' AND label = 'Instagram'
           AND merged_into IS NULL"""
    ).fetchone()
    if row is None:
        raise ValidationError("Platform-Entity 'Instagram' fehlt (Seed 0004)")
    return str(row["id"])


def _to_generic(
    conn: psycopg.Connection, rows: list[dict[str, Any]], *, with_statements: bool
) -> list[dict[str, Any]]:
    """Username-Rows → generische Snapshot-Rows. statements (handle, platform,
    name) nur für den Commit — der Kern committet sie bei Neuanlage."""
    platform_id = _instagram_platform_id(conn) if with_statements else None
    out: list[dict[str, Any]] = []
    for r in rows:
        if "status" in r:
            out.append(r)
            continue
        g: dict[str, Any] = {
            **r,
            "type_id": "SocialMediaAccount",
            "label": r["username"],
            "identifiers": {"account_uri": f"instagram:{r['username']}"},
        }
        if with_statements:
            stmts = [
                {"predicate_id": "handle",
                 "value": {"type": "string", "text": r["username"]}},
                {"predicate_id": "platform",
                 "value": {"type": "entity", "object_id": platform_id}},
            ]
            if r["display_name"]:
                stmts.append({"predicate_id": "name",
                              "value": {"type": "string", "text": r["display_name"]}})
            g["statements"] = stmts
        out.append(g)
    return out


def _map_status(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for r in rows:
        r["status"] = _STATUS_MAP.get(r["status"], r["status"])
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
    generic = _to_generic(conn, _normalize_rows(rows), with_statements=False)
    result = preview_snapshot(
        conn, predicate_id="follows", owner_entity_id=owner_entity_id,
        rows=generic, direction=_DIRECTION[direction],
    )
    mapped = _map_status(result["rows"])
    return {"rows": mapped, "summary": _summary(mapped)}


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
    generic = _to_generic(conn, _normalize_rows(rows), with_statements=True)
    result = commit_snapshot(
        conn, predicate_id="follows", owner_entity_id=owner_entity_id,
        rows=generic, direction=_DIRECTION[direction],
        observed_at=observed_at, agent=agent, activity="follower_list_import",
    )
    return {
        "source_id": result["source_id"],
        "accounts_created": result["entities_created"],
        "follows_created": result["statements_created"],
        "follows_confirmed": result["statements_confirmed"],
        "skipped_invalid": result["skipped_invalid"],
    }
