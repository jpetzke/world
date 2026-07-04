"""Single-User-Auth für den Web-Betrieb.

Login setzt eine signierte Server-Session (Starlette ``SessionMiddleware``).
Credentials liegen im Env (``AUTH_USERNAME`` / ``AUTH_PASSWORD``, Klartext) —
das ist reine App-Schicht, kein Eingriff ins Substrat (Invarianten unberührt).
"""

import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import api_keys
from .config import get_auth_password, get_auth_username
from .db import db

# --- Brute-Force-Lockout (in-memory, pro Client-IP) ------------------------
# Single-Worker-Annahme: der State lebt im Prozess. Für einen privaten
# Single-User-Dienst ausreichend. Bei mehreren Workern gälte das Limit pro
# Worker — deshalb im Deploy-Tutorial: 1 uvicorn-Worker.

_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 15 * 60
_failures: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    # uvicorn --proxy-headers setzt request.client.host bereits auf die echte
    # Client-IP (aus X-Forwarded-For des vertrauenswürdigen Proxys).
    return request.client.host if request.client else "unknown"


def _is_locked(ip: str) -> bool:
    hits = [t for t in _failures.get(ip, []) if time.monotonic() - t < _LOCKOUT_SECONDS]
    _failures[ip] = hits
    return len(hits) >= _MAX_FAILURES


def _record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.monotonic())


def _check_credentials(username: str, password: str) -> bool:
    exp_user = get_auth_username()
    exp_pass = get_auth_password()
    if not exp_user or not exp_pass:
        return False
    # constant-time über beide Felder — kein Early-Return-Timing-Leak.
    ok_user = hmac.compare_digest(username.encode(), exp_user.encode())
    ok_pass = hmac.compare_digest(password.encode(), exp_pass.encode())
    return ok_user and ok_pass


class LoginPayload(BaseModel):
    username: str
    password: str


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(payload: LoginPayload, request: Request):
    ip = _client_ip(request)
    if _is_locked(ip):
        raise HTTPException(
            status_code=429, detail="Zu viele Fehlversuche. Bitte später erneut."
        )
    if not _check_credentials(payload.username, payload.password):
        _record_failure(ip)
        raise HTTPException(status_code=401, detail="Falsche Zugangsdaten.")
    _failures.pop(ip, None)
    request.session["user"] = payload.username
    return {"authenticated": True, "username": payload.username}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"authenticated": False}


@router.get("/me")
def me(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Nicht angemeldet.")
    return {"authenticated": True, "username": user}


def require_auth(request: Request) -> None:
    """Dependency-Gate für Session-only-Routen (u. a. Key-Verwaltung)."""
    if not request.session.get("user"):
        raise HTTPException(status_code=401, detail="Nicht angemeldet.")


# --- API-Key-Gate (Scope-Hierarchie read < write < admin) -------------------


def _request_secret(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return request.headers.get("x-api-key")


def require_scope(minimum: str):
    """Dependency-Factory: Session = Vollzugriff, sonst API-Key mit Scope.

    Der Key kommt als ``Authorization: Bearer wm_…`` oder ``X-API-Key``.
    Kein Lockout nötig — Secrets sind 288-Bit-Zufall, nicht ratbar.
    """

    def dependency(request: Request, conn=Depends(db)) -> None:
        if request.session.get("user"):
            return
        secret = _request_secret(request)
        if not secret:
            raise HTTPException(status_code=401, detail="Nicht angemeldet.")
        key = api_keys.resolve_secret(conn, secret)
        if key is None:
            raise HTTPException(status_code=401, detail="Ungültiger API-Key.")
        if not api_keys.scope_covers(key["scope"], minimum):
            raise HTTPException(
                status_code=403,
                detail=f"Scope '{key['scope']}' reicht nicht — '{minimum}' nötig.",
            )

    return dependency
