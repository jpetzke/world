"""Registry-Bridge: WorldAI ruft die MCP-Tools in-process auf.

Quelle der Tool-Schemas ist dieselbe FastMCP-Registry, aus der der
MCP-Server seine Tools zieht — neue Server-Tools sind damit automatisch
für WorldAI sichtbar, hartkodierte Tool-Definitionen gibt es nicht.

Der Verfassungs-Session-Lock des Servers gilt unverändert: pro Chat-Session
wird ein synthetischer Access-Token gesetzt (auth-Contextvar), sodass
``_require_write`` im MCP-Server greift — das Modell muss welt_constitution
einmal pro Session aufrufen, bevor Schreib-Tools freigeschaltet sind.
"""

import hashlib
from typing import Any

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp.exceptions import ToolError

from ..mcp_server import mcp

# Namens-Präfixe der Schreib-Tools: jeder Call pausiert den Loop und
# verlangt eine UI-Bestätigung, bevor er ausgeführt wird.
WRITE_PREFIXES = (
    "welt_commit",
    "welt_create",
    "welt_merge",
    "welt_fix",
    "welt_deprecate",
    "welt_set_rank",
    "welt_decide",
    "welt_import",
    "welt_ingest",
    "welt_propose",
    "welt_amend",
)


def is_write_tool(name: str) -> bool:
    return name.startswith(WRITE_PREFIXES)


def llm_tool_schemas() -> list[dict[str, Any]]:
    """Tool-Definitionen fürs LLM (OpenAI-Format), zur Laufzeit aus der
    FastMCP-Registry generiert (Name, Description, Parameter-JSON-Schema)."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters,
            },
        }
        for tool in mcp._tool_manager.list_tools()
    ]


def session_token(session_id: str) -> str:
    return f"worldai:{session_id}"


def session_ack_key(session_id: str) -> str:
    """Ack-Key des Verfassungs-Gates für eine WorldAI-Session (Testbarkeit)."""
    return hashlib.sha256(session_token(session_id).encode()).hexdigest()


async def execute_tool(name: str, arguments: dict[str, Any], session_id: str) -> Any:
    """Ein MCP-Tool in-process ausführen — mit demselben Auth-Kontext-Mechanismus
    wie der HTTP-Transport, damit Verfassungs-Gate und Scope-Checks greifen."""
    token = AccessToken(
        token=session_token(session_id),
        client_id="worldai",
        scopes=["welt:read", "welt:write"],
    )
    ctx_token = auth_context_var.set(AuthenticatedUser(token))
    try:
        return await mcp._tool_manager.call_tool(name, arguments)
    finally:
        auth_context_var.reset(ctx_token)


__all__ = [
    "WRITE_PREFIXES",
    "ToolError",
    "execute_tool",
    "is_write_tool",
    "llm_tool_schemas",
    "session_ack_key",
    "session_token",
]
