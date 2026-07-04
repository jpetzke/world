"""Konfiguration über Umgebungsvariablen (+ .env im Repo-Root, gitignored)."""

import os
from pathlib import Path

DEFAULT_DSN = "postgresql://weltmodell:weltmodell@localhost:5433/weltmodell"
DEFAULT_LLM_MODEL = "poolside/laguna-xs-2.1:free"

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_dotenv() -> None:
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def get_dsn() -> str:
    return os.environ.get("WELTMODELL_DSN", DEFAULT_DSN)


def get_openrouter_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY")


def get_llm_model() -> str:
    return os.environ.get("WELTMODELL_LLM_MODEL", DEFAULT_LLM_MODEL)


# --- Deployment / Auth (Web-Betrieb) ---------------------------------------


def is_prod() -> bool:
    """Prod-Modus: erzwingt Secure-Cookies, deaktiviert Docs/Dev-CORS.

    Alles außer WELTMODELL_ENV=production gilt als Entwicklung.
    """
    return os.environ.get("WELTMODELL_ENV", "development").lower() == "production"


def get_public_url() -> str:
    """Öffentliche Basis-URL (OAuth-Issuer + MCP-Resource), ohne Trailing-Slash.

    In Produktion die externe Domain setzen (z. B. https://world.jshift.de) —
    daraus entstehen die absoluten URLs in der OAuth-Metadata und die
    erlaubten Hosts der DNS-Rebinding-Protection.
    """
    return os.environ.get("PUBLIC_URL", "http://localhost:8100").rstrip("/")


def get_auth_username() -> str | None:
    return os.environ.get("AUTH_USERNAME")


def get_auth_password() -> str | None:
    return os.environ.get("AUTH_PASSWORD")


def get_session_secret() -> str:
    """Signierschlüssel fürs Session-Cookie.

    In Prod Pflicht (Fail-fast) — ein geratenes Default wäre ein
    Session-Forgery-Loch. In Dev fällt ein festes, offensichtlich
    unsicheres Default ein, damit lokal ohne Setup gearbeitet werden kann.
    """
    secret = os.environ.get("SESSION_SECRET")
    if secret:
        return secret
    if is_prod():
        raise RuntimeError(
            "SESSION_SECRET fehlt. In Produktion Pflicht — mit "
            "`python -c 'import secrets; print(secrets.token_urlsafe(48))'` erzeugen."
        )
    return "dev-insecure-session-secret-do-not-use-in-production"
