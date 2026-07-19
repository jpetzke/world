"""Instagram-JSON-Ingest — Upload des Scraper-Formats (metadata + users[]).

Der Scraper (extract_instagram.py) schreibt pro (Account, followers|following)
eine JSON-Datei: `metadata` trägt Owner-Handle, Owner-pk (user_id), Richtung
(kind), Aufnahmezeit (captured_at), gemeldete Zahl (expected_total) und status;
`users[]` die rohen Instagram-User-Objekte.

Gegenüber dem Paste-Import (follower_import.py):
- Die Datei ist selbstbeschreibend — Owner/Richtung/Zeit kommen aus `metadata`,
  nichts wird manuell gewählt; der Owner wird get-or-create angelegt.
- Jeder User trägt eine stabile pk → identifying `instagram_pk` (wenn vorhanden;
  sonst `account_uri` als Fallback). Auflösung rein deterministisch über harte
  Keys, NIE Fuzzy-Namens-Merge (IG-Display-Namen kollidieren) — deshalb
  resolve(label=None): die Vektorstufe läuft nur mit Label.
- Attribute name/is_verified/is_private werden bei Neuanlage geloggt; die
  gemeldeten Zahlen als number mit valid_from=captured_at (Zeitreihe).

Snapshot-Philosophie (Verfassung): der status ist egal — was in `users[]` steht,
gilt und wird re-bestätigt statt dupliziert; Abwesenheit ist kein Gegenbeweis
(kein Unfollow-Tracking). Leere/partielle Dateien (rate-limited) tragen trotzdem
Owner + gemeldete Zahl.
"""

import re
from typing import Any

import psycopg

from .entities import create_entity
from .errors import ValidationError
from .pipeline import ingest_document
from .resolution import resolve
from .statements import commit_statement

USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")

# followers = die Row-Accounts folgen dem Owner (Owner ist Objekt) → incoming.
_DIRECTION = {"followers": "incoming", "following": "outgoing"}
# kind → Prädikat für die gemeldete Zahl des Owners.
_COUNT_PREDICATE = {"followers": "follower_count", "following": "following_count"}


def _instagram_platform_id(conn: psycopg.Connection) -> str:
    row = conn.execute(
        """SELECT id FROM entity WHERE type_id = 'Platform' AND label = 'Instagram'
           AND merged_into IS NULL"""
    ).fetchone()
    if row is None:
        raise ValidationError("Platform-Entity 'Instagram' fehlt (Seed 0004)")
    return str(row["id"])


def _clean_username(raw: Any) -> str:
    return str(raw or "").strip().lstrip("@").lower()


def _clean_pk(u: dict[str, Any]) -> str | None:
    pk = u.get("pk") or u.get("pk_id") or u.get("id")
    pk = str(pk).strip() if pk not in (None, "") else ""
    return pk or None


def _account_identifiers(username: str, pk: str | None) -> dict[str, str]:
    """pk zuerst (stärkerer Anker), account_uri als Fallback. resolve matcht
    deterministisch über den ERSTEN Treffer in dieser Reihenfolge."""
    ids: dict[str, str] = {}
    if pk:
        ids["instagram_pk"] = pk
    ids["account_uri"] = f"instagram:{username}"
    return ids


def _normalize_user(u: Any) -> dict[str, Any]:
    """Rohes IG-User-Objekt → die modellierten Felder. Ungültige Rows werden
    markiert (status='invalid'), nie verworfen — der Preview zeigt sie."""
    if not isinstance(u, dict):
        return {"username": "", "status": "invalid", "reason": "kein Objekt"}
    username = _clean_username(u.get("username"))
    if not USERNAME_RE.match(username):
        return {"username": username, "status": "invalid",
                "reason": "kein gültiger Instagram-Username"}
    return {
        "username": username,
        "full_name": (str(u.get("full_name") or "").strip() or None),
        "pk": _clean_pk(u),
        "is_verified": 1 if u.get("is_verified") else 0,
        "is_private": 1 if u.get("is_private") else 0,
    }


def parse_file(data: Any) -> dict[str, Any]:
    """Scraper-Datei validieren + normalisieren. Wirft ValidationError, wenn
    Owner/Richtung nicht bestimmbar sind (Datei unbrauchbar); Row-Fehler sind
    dagegen nur Markierungen."""
    if not isinstance(data, dict) or not isinstance(data.get("metadata"), dict):
        raise ValidationError("Kein gültiges Scraper-JSON (metadata-Block fehlt)")
    meta = data["metadata"]
    kind = meta.get("kind")
    if kind not in _DIRECTION:
        raise ValidationError(
            f"metadata.kind muss 'followers' oder 'following' sein, ist {kind!r}"
        )
    owner_username = _clean_username(meta.get("handle"))
    if not USERNAME_RE.match(owner_username):
        raise ValidationError(
            f"metadata.handle ist kein gültiger Username: {meta.get('handle')!r}"
        )
    users = data.get("users")
    if not isinstance(users, list):
        users = []

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for u in users:
        row = _normalize_user(u)
        if row.get("status") == "invalid":
            rows.append(row)
            continue
        if row["username"] in seen:
            continue  # In-Batch-Duplikat
        seen.add(row["username"])
        rows.append(row)

    expected = meta.get("expected_total")
    return {
        "kind": kind,
        "direction": _DIRECTION[kind],
        "owner": {"username": owner_username, "pk": _clean_pk({"pk": meta.get("user_id")})},
        "captured_at": meta.get("captured_at"),
        "status": meta.get("status"),
        "expected_total": int(expected) if isinstance(expected, (int, float)) else None,
        "rows": rows,
    }


# --- Resolution (rein deterministisch) --------------------------------------

def _resolve_account(conn: psycopg.Connection, username: str, pk: str | None) -> str | None:
    res = resolve(
        conn, type_id="SocialMediaAccount", label=None,
        identifiers=_account_identifiers(username, pk),
    )
    return res["match"]


def _follows_exists(
    conn: psycopg.Connection, owner_id: str, account_id: str, direction: str
) -> bool:
    subject_id, object_id = (
        (owner_id, account_id) if direction == "outgoing" else (account_id, owner_id)
    )
    return conn.execute(
        """SELECT 1 FROM statement
           WHERE predicate_id = 'follows' AND subject_id = %s AND object_id = %s
             AND system_to IS NULL AND rank <> 'deprecated' LIMIT 1""",
        (subject_id, object_id),
    ).fetchone() is not None


# --- Commit -----------------------------------------------------------------

def _backfill_pk(conn: psycopg.Connection, entity_id: str, pk: str, source_id: str) -> None:
    """pk an einen über account_uri gematchten Alt-Account nachtragen.
    commit_statement re-bestätigt bei Gleichheit; ein pk-Konflikt (pk gehört
    schon anderem Account, z. B. Username-Recycling) wirft — dann NICHT
    nachtragen statt den ganzen Import zu kippen."""
    try:
        commit_statement(
            conn, subject_id=entity_id, predicate_id="instagram_pk",
            value={"type": "string", "text": pk}, source_ids=[source_id],
        )
    except ValidationError:
        pass


def _get_or_create_account(
    conn: psycopg.Connection, *, username: str, full_name: str | None,
    pk: str | None, is_verified: int, is_private: int,
    platform_id: str, source_id: str,
) -> tuple[str, bool]:
    match = _resolve_account(conn, username, pk)
    if match:
        if pk:
            _backfill_pk(conn, match, pk, source_id)
        return match, False

    entity_id = str(create_entity(conn, type_id="SocialMediaAccount", label=username)["id"])
    for pred_id, val in _account_identifiers(username, pk).items():
        commit_statement(conn, subject_id=entity_id, predicate_id=pred_id,
                         value={"type": "string", "text": val}, source_ids=[source_id])
    commit_statement(conn, subject_id=entity_id, predicate_id="handle",
                     value={"type": "string", "text": username}, source_ids=[source_id])
    commit_statement(conn, subject_id=entity_id, predicate_id="platform",
                     value={"type": "entity", "object_id": platform_id}, source_ids=[source_id])
    if full_name:
        commit_statement(conn, subject_id=entity_id, predicate_id="name",
                         value={"type": "string", "text": full_name}, source_ids=[source_id])
    commit_statement(conn, subject_id=entity_id, predicate_id="is_verified",
                     value={"type": "number", "number": is_verified}, source_ids=[source_id])
    commit_statement(conn, subject_id=entity_id, predicate_id="is_private",
                     value={"type": "number", "number": is_private}, source_ids=[source_id])
    return entity_id, True


def _log_owner_count(
    conn: psycopg.Connection, owner_id: str, parsed: dict[str, Any], source_id: str
) -> None:
    total = parsed["expected_total"]
    if total is None:
        return
    commit_statement(
        conn, subject_id=owner_id, predicate_id=_COUNT_PREDICATE[parsed["kind"]],
        value={"type": "number", "number": total}, source_ids=[source_id],
        valid_from=parsed["captured_at"],
    )


def _raw_provenance(filename: str, parsed: dict[str, Any]) -> dict[str, Any]:
    """Schlank, aber rekonstruierbar: metadata + die modellierten Row-Felder."""
    return {
        "kind": "instagram_json_import",
        "filename": filename,
        "handle": parsed["owner"]["username"],
        "owner_pk": parsed["owner"]["pk"],
        "direction": parsed["direction"],
        "captured_at": parsed["captured_at"],
        "status": parsed["status"],
        "expected_total": parsed["expected_total"],
        "users": [
            {k: r.get(k) for k in ("username", "pk", "full_name", "is_verified", "is_private")}
            for r in parsed["rows"] if r.get("status") != "invalid"
        ],
    }


def commit_file(
    conn: psycopg.Connection, *, filename: str, data: Any,
    agent: str = "ui:instagram-json-import",
) -> dict[str, Any]:
    """Eine Quelle pro Datei; Owner get-or-create + gemeldete Zahl, dann pro Row
    Account (get-or-create) + follows bzw. Re-Bestätigung. Jede Row in einem
    Savepoint — eine kollidierende Row kippt nicht den ganzen Import."""
    parsed = parse_file(data)
    platform_id = _instagram_platform_id(conn)
    direction = parsed["direction"]

    doc = ingest_document(
        conn, raw=_raw_provenance(filename, parsed),
        activity="instagram_json_import", agent=agent,
        retrieved_at=parsed["captured_at"],
    )
    sid = str(doc["id"])

    owner = parsed["owner"]
    owner_id, owner_created = _get_or_create_account(
        conn, username=owner["username"], full_name=None, pk=owner["pk"],
        is_verified=0, is_private=0, platform_id=platform_id, source_id=sid,
    )
    _log_owner_count(conn, owner_id, parsed, sid)

    counts = {"accounts_created": int(owner_created), "follows_created": 0,
              "follows_confirmed": 0, "skipped_invalid": 0, "skipped_conflict": 0}

    for row in parsed["rows"]:
        if row.get("status") == "invalid":
            counts["skipped_invalid"] += 1
            continue
        try:
            with conn.transaction():
                account_id, created = _get_or_create_account(
                    conn, username=row["username"], full_name=row["full_name"],
                    pk=row["pk"], is_verified=row["is_verified"],
                    is_private=row["is_private"], platform_id=platform_id, source_id=sid,
                )
                if account_id == owner_id:
                    counts["skipped_invalid"] += 1  # Owner in eigener Liste
                    continue
                if created:
                    counts["accounts_created"] += 1
                subject_id, object_id = (
                    (owner_id, account_id) if direction == "outgoing"
                    else (account_id, owner_id)
                )
                res = commit_statement(
                    conn, subject_id=subject_id, predicate_id="follows",
                    value={"type": "entity", "object_id": object_id},
                    source_ids=[sid], valid_from=parsed["captured_at"],
                )
                if "reconfirmed" in (res.get("flags") or []):
                    counts["follows_confirmed"] += 1
                else:
                    counts["follows_created"] += 1
        except ValidationError:
            counts["skipped_conflict"] += 1

    return {
        "filename": filename,
        "source_id": sid,
        "owner_handle": owner["username"],
        "owner_created": owner_created,
        "direction": parsed["kind"],
        "captured_at": parsed["captured_at"],
        "status": parsed["status"],
        "expected_total": parsed["expected_total"],
        "rows_total": len([r for r in parsed["rows"] if r.get("status") != "invalid"]),
        **counts,
    }


# --- Preview (read-only) ----------------------------------------------------

def preview_file(conn: psycopg.Connection, *, filename: str, data: Any) -> dict[str, Any]:
    """Read-only: klassifiziert eine Datei gegen den Bestand. Schreibt nichts."""
    parsed = parse_file(data)
    direction = parsed["direction"]
    owner = parsed["owner"]
    owner_id = _resolve_account(conn, owner["username"], owner["pk"])

    valid = invalid = accounts_new = accounts_existing = 0
    follows_new = follows_confirmed = 0
    for row in parsed["rows"]:
        if row.get("status") == "invalid":
            invalid += 1
            continue
        valid += 1
        account_id = _resolve_account(conn, row["username"], row["pk"])
        if account_id is None:
            accounts_new += 1
            follows_new += 1
        elif account_id == owner_id:
            valid -= 1  # Owner selbst — keine echte Row
        else:
            accounts_existing += 1
            if owner_id and _follows_exists(conn, owner_id, account_id, direction):
                follows_confirmed += 1
            else:
                follows_new += 1

    return {
        "filename": filename,
        "owner_handle": owner["username"],
        "owner_exists": owner_id is not None,
        "direction": parsed["kind"],
        "captured_at": parsed["captured_at"],
        "status": parsed["status"],
        "expected_total": parsed["expected_total"],
        "rows_total": valid + invalid,
        "valid": valid,
        "invalid": invalid,
        "accounts_new": accounts_new,
        "accounts_existing": accounts_existing,
        "follows_new": follows_new,
        "follows_confirmed": follows_confirmed,
    }


# --- Batch (mehrere Dateien) ------------------------------------------------

def _totals(results: list[dict[str, Any]], keys: list[str]) -> dict[str, int]:
    return {k: sum(int(r.get(k) or 0) for r in results if "error" not in r) for k in keys}


def preview_files(conn: psycopg.Connection, items: list[dict[str, Any]]) -> dict[str, Any]:
    """items: [{"filename", "data"}]. Pro Datei eine Zusammenfassungszeile;
    strukturell kaputte Dateien werden als {filename, error} gemeldet, nie
    kippt eine Datei den ganzen Batch."""
    files: list[dict[str, Any]] = []
    for it in items:
        if it.get("error"):
            files.append({"filename": it["filename"], "error": it["error"]})
            continue
        try:
            files.append(preview_file(conn, filename=it["filename"], data=it.get("data")))
        except ValidationError as e:
            files.append({"filename": it["filename"], "error": str(e)})
    totals = _totals(files, ["rows_total", "valid", "invalid", "accounts_new",
                             "accounts_existing", "follows_new", "follows_confirmed"])
    totals["files"] = len(files)
    totals["files_failed"] = sum(1 for f in files if "error" in f)
    return {"files": files, "totals": totals}


def commit_files(
    conn: psycopg.Connection, items: list[dict[str, Any]],
    agent: str = "ui:instagram-json-import",
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for it in items:
        if it.get("error"):
            files.append({"filename": it["filename"], "error": it["error"]})
            continue
        try:
            files.append(commit_file(conn, filename=it["filename"],
                                     data=it.get("data"), agent=agent))
        except ValidationError as e:
            files.append({"filename": it["filename"], "error": str(e)})
    totals = _totals(files, ["accounts_created", "follows_created", "follows_confirmed",
                             "skipped_invalid", "skipped_conflict"])
    totals["files"] = len(files)
    totals["files_failed"] = sum(1 for f in files if "error" in f)
    return {"files": files, "totals": totals}
