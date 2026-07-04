"""Eingebetteter OAuth-2.1-Authorization-Server für den MCP-Zugang.

Das MCP-SDK macht die Protokoll-Validierung (PKCE, DCR, redirect_uri);
dieser Provider speichert/lädt nur Zustand — komplett in Postgres, damit
laufende Flows Deploys überleben. Tokens sind opak und geprefixt
(``welt_at_…``), gespeichert werden nur SHA-256-Hashes.

Der Login selbst ist eine eigene FastAPI-Route (``/oauth/login``): /authorize
parkt den Request als Transaktion, der Nutzer authentifiziert sich mit den
bestehenden Single-User-Credentials (inkl. Brute-Force-Lockout aus auth.py),
danach wird ein Single-Use-Code gemünzt. Nach dem Formular-POST antwortet der
Server mit 200 + Meta-Refresh statt 302 — ein 302 in der Form-Navigation wird
von CSP ``form-action 'self'`` geblockt und strandet den Nutzer.
"""

import hashlib
import html
import json
import secrets
from functools import partial
from typing import Any

import anyio
import psycopg
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from . import auth
from .config import get_auth_username, get_public_url
from .db import get_conn

SCOPES = ["welt:read", "welt:write"]

ACCESS_TTL = 4 * 3600
REFRESH_TTL = 60 * 24 * 3600
CODE_TTL = 10 * 60
TXN_TTL = 15 * 60


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _tx(fn):
    conn = get_conn()
    try:
        result = fn(conn)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def _db(fn):
    """Sync-DB-Arbeit aus async Provider-Methoden — nie den Event-Loop blocken."""
    return await anyio.to_thread.run_sync(partial(_tx, fn))


# --- Token-Minting (sync, läuft immer im Thread) -----------------------------


def _mint_tokens(
    conn: psycopg.Connection,
    *,
    client_id: str,
    subject: str,
    scopes: list[str],
    resource: str | None,
) -> OAuthToken:
    access = f"welt_at_{secrets.token_urlsafe(32)}"
    refresh = f"welt_rt_{secrets.token_urlsafe(32)}"
    conn.execute(
        """INSERT INTO mcp_token (token_hash, kind, client_id, subject, scopes, data, expires_at)
           VALUES (%s, 'access', %s, %s, %s, %s::jsonb, now() + make_interval(secs => %s)),
                  (%s, 'refresh', %s, %s, %s, '{}'::jsonb, now() + make_interval(secs => %s))""",
        (
            _sha256(access), client_id, subject, scopes,
            json.dumps({"resource": resource}), ACCESS_TTL,
            _sha256(refresh), client_id, subject, scopes, REFRESH_TTL,
        ),
    )
    # Abgelaufenes opportunistisch räumen — kein eigener Cleanup-Job nötig.
    conn.execute("DELETE FROM mcp_token WHERE expires_at < now()")
    conn.execute("DELETE FROM mcp_authorize_txn WHERE expires_at < now()")
    return OAuthToken(
        access_token=access,
        token_type="Bearer",
        expires_in=ACCESS_TTL,
        scope=" ".join(scopes),
        refresh_token=refresh,
    )


class WeltOAuthProvider:
    """Storage-Backend für die SDK-OAuth-Handler (alles in Postgres)."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        def q(conn):
            return conn.execute(
                "SELECT registration FROM mcp_oauth_client WHERE client_id = %s",
                (client_id,),
            ).fetchone()

        row = await _db(q)
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate(row["registration"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        payload = client_info.model_dump(mode="json")

        def q(conn):
            conn.execute(
                """INSERT INTO mcp_oauth_client (client_id, registration)
                   VALUES (%s, %s::jsonb)
                   ON CONFLICT (client_id) DO UPDATE SET registration = EXCLUDED.registration""",
                (client_info.client_id, json.dumps(payload)),
            )

        await _db(q)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Parkt den Request als Transaktion und schickt den Browser zum Login.

        Die Transaktion wird NICHT beim ersten Submit konsumiert (nur Codes
        sind single-use) — sonst endet jede geblockte Navigation oder ein
        Doppelklick in „Link abgelaufen".
        """
        txn = {
            "state": params.state,
            "scopes": params.scopes or SCOPES,
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource,
        }

        def q(conn):
            return conn.execute(
                """INSERT INTO mcp_authorize_txn (client_id, params, expires_at)
                   VALUES (%s, %s::jsonb, now() + make_interval(secs => %s))
                   RETURNING id""",
                (client.client_id, json.dumps(txn), TXN_TTL),
            ).fetchone()

        row = await _db(q)
        return f"{get_public_url()}/oauth/login?txn={row['id']}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        def q(conn):
            return conn.execute(
                """SELECT * FROM mcp_token
                   WHERE token_hash = %s AND kind = 'code' AND client_id = %s
                     AND expires_at > now()""",
                (_sha256(authorization_code), client.client_id),
            ).fetchone()

        row = await _db(q)
        if row is None:
            return None
        data = row["data"]
        return AuthorizationCode(
            code=authorization_code,
            scopes=row["scopes"],
            expires_at=row["expires_at"].timestamp(),
            client_id=row["client_id"],
            code_challenge=data["code_challenge"],
            redirect_uri=data["redirect_uri"],
            redirect_uri_provided_explicitly=data["redirect_uri_provided_explicitly"],
            resource=data.get("resource"),
            subject=row["subject"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        code_hash = _sha256(authorization_code.code)

        def q(conn):
            conn.execute("DELETE FROM mcp_token WHERE token_hash = %s", (code_hash,))
            return _mint_tokens(
                conn,
                client_id=client.client_id,
                subject=authorization_code.subject or get_auth_username() or "user",
                scopes=authorization_code.scopes,
                resource=authorization_code.resource,
            )

        return await _db(q)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        def q(conn):
            return conn.execute(
                """SELECT * FROM mcp_token
                   WHERE token_hash = %s AND kind = 'refresh' AND client_id = %s
                     AND expires_at > now()""",
                (_sha256(refresh_token), client.client_id),
            ).fetchone()

        row = await _db(q)
        if row is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=int(row["expires_at"].timestamp()),
            subject=row["subject"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        rt_hash = _sha256(refresh_token.token)

        def q(conn):
            # Rotation: alter Refresh-Token stirbt beim Einlösen.
            conn.execute("DELETE FROM mcp_token WHERE token_hash = %s", (rt_hash,))
            return _mint_tokens(
                conn,
                client_id=client.client_id,
                subject=refresh_token.subject or get_auth_username() or "user",
                scopes=scopes or refresh_token.scopes,
                resource=None,
            )

        return await _db(q)

    async def load_access_token(self, token: str) -> AccessToken | None:
        def q(conn):
            return conn.execute(
                """SELECT * FROM mcp_token
                   WHERE token_hash = %s AND kind = 'access' AND expires_at > now()""",
                (_sha256(token),),
            ).fetchone()

        row = await _db(q)
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=int(row["expires_at"].timestamp()),
            subject=row["subject"],
            resource=row["data"].get("resource"),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        def q(conn):
            conn.execute(
                "DELETE FROM mcp_token WHERE token_hash = %s", (_sha256(token.token),)
            )

        await _db(q)


# --- Login-Seite (/oauth/login) ----------------------------------------------

login_router = APIRouter(include_in_schema=False)


def _load_txn(conn: psycopg.Connection, txn_id: str) -> dict[str, Any] | None:
    try:
        return conn.execute(
            """SELECT id, client_id, params FROM mcp_authorize_txn
               WHERE id = %s AND expires_at > now()""",
            (txn_id,),
        ).fetchone()
    except psycopg.errors.InvalidTextRepresentation:
        return None  # txn ist kein UUID — wie „nicht gefunden" behandeln


def _complete_authorization(conn: psycopg.Connection, txn: dict[str, Any]) -> str:
    """Münzt einen Single-Use-Code und baut die Callback-URL (echot state)."""
    params = txn["params"]
    code = f"welt_code_{secrets.token_urlsafe(32)}"
    conn.execute(
        """INSERT INTO mcp_token (token_hash, kind, client_id, subject, scopes, data, expires_at)
           VALUES (%s, 'code', %s, %s, %s, %s::jsonb, now() + make_interval(secs => %s))""",
        (
            _sha256(code), txn["client_id"], get_auth_username() or "user",
            params["scopes"],
            json.dumps({
                "code_challenge": params["code_challenge"],
                "redirect_uri": params["redirect_uri"],
                "redirect_uri_provided_explicitly": params["redirect_uri_provided_explicitly"],
                "resource": params.get("resource"),
            }),
            CODE_TTL,
        ),
    )
    return construct_redirect_uri(
        params["redirect_uri"], code=code, state=params.get("state")
    )


_PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weltmodell — MCP-Zugriff</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #111; color: #eee;
         display: flex; justify-content: center; padding-top: 12vh; }}
  main {{ width: 22rem; }}
  h1 {{ font-size: 1.1rem; font-weight: 600; }}
  input {{ width: 100%; box-sizing: border-box; margin: .3rem 0 .8rem;
           padding: .55rem; border-radius: 6px; border: 1px solid #444;
           background: #1c1c1c; color: #eee; }}
  button {{ width: 100%; padding: .6rem; border: 0; border-radius: 6px;
            background: #3b82f6; color: #fff; font-weight: 600; cursor: pointer; }}
  .err {{ color: #f87171; margin-bottom: .8rem; }}
  a {{ color: #60a5fa; }}
</style></head><body><main>{body}</main></body></html>"""


def _login_form(txn_id: str, error: str | None = None) -> str:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return _PAGE.format(body=f"""
<h1>Weltmodell — Zugriff für einen MCP-Client freigeben</h1>
{err}
<form method="post" action="/oauth/login">
  <input type="hidden" name="txn" value="{html.escape(txn_id)}">
  <label>Benutzername <input name="username" autocomplete="username" required></label>
  <label>Passwort <input name="password" type="password"
         autocomplete="current-password" required></label>
  <button type="submit">Freigeben</button>
</form>""")


def _error_page(message: str) -> str:
    return _PAGE.format(body=f"<h1>Fehler</h1><p>{html.escape(message)}</p>")


def _success_page(redirect_url: str) -> str:
    """200 + Meta-Refresh statt 302: immun gegen CSP form-action in der
    Form-Navigations-Kette und zugleich sichtbares „Verbunden"-Feedback."""
    safe = html.escape(redirect_url, quote=True)
    return _PAGE.format(body=f"""
<meta http-equiv="refresh" content="0;url={safe}">
<h1>Verbunden ✓</h1>
<p>Zugriff freigegeben. Du wirst zurückgeleitet …</p>
<p><a href="{safe}">Weiter, falls nichts passiert</a></p>""")


@login_router.get("/oauth/login", response_class=HTMLResponse)
def oauth_login_form(txn: str):
    def q(conn):
        return _load_txn(conn, txn)

    if _tx(q) is None:
        return HTMLResponse(
            _error_page("Anfrage abgelaufen — bitte die Verbindung im MCP-Client neu starten."),
            status_code=400,
        )
    return HTMLResponse(_login_form(txn))


@login_router.post("/oauth/login", response_class=HTMLResponse)
def oauth_login_submit(
    request: Request,
    txn: str = Form(),
    username: str = Form(),
    password: str = Form(),
):
    # Gleicher Lockout wie der App-Login (bewusst geteilter State pro IP).
    ip = auth._client_ip(request)
    if auth._is_locked(ip):
        return HTMLResponse(
            _error_page("Zu viele Fehlversuche. Bitte später erneut."), status_code=429
        )

    def load(conn):
        return _load_txn(conn, txn)

    txn_row = _tx(load)
    if txn_row is None:
        return HTMLResponse(
            _error_page("Anfrage abgelaufen — bitte die Verbindung im MCP-Client neu starten."),
            status_code=400,
        )

    if not auth._check_credentials(username, password):
        auth._record_failure(ip)
        return HTMLResponse(
            _login_form(txn, "Falsche Zugangsdaten."), status_code=401
        )
    auth._failures.pop(ip, None)

    def complete(conn):
        return _complete_authorization(conn, txn_row)

    return HTMLResponse(_success_page(_tx(complete)))
