"""WorldAI-Endpoints unter /api/ai — Session-Auth (Single-User-UI).

Der Turn-Endpoint streamt den Agent-Loop als SSE: Text-Tokens,
Tool-Call-Start, Ergebnis-Digests, Schreib-Gate-Bestätigungen, Fehler.
"""

import json
import os

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..db import db
from ..errors import WeltmodellError
from . import providers, sessions
from .agent import AgentTurn

router = APIRouter(prefix="/ai", tags=["ai"])


class SessionCreate(BaseModel):
    model: str | None = None


class MessageIn(BaseModel):
    text: str
    model: str | None = None


class ConfirmIn(BaseModel):
    tool_call_id: str
    approved: bool


@router.get("/config")
def get_config():
    return {
        "provider": os.environ.get("MODEL_PROVIDER", "openrouter"),
        "default_model": providers.get_model_id(),
        "models": providers.get_ui_models(),
    }


@router.get("/sessions")
def get_sessions(conn=Depends(db)):
    return sessions.list_sessions(conn)


@router.post("/sessions", status_code=201)
def post_session(payload: SessionCreate, conn=Depends(db)):
    return sessions.create_session(conn, model=payload.model)


@router.get("/sessions/{session_id}")
def get_session_detail(session_id: str, conn=Depends(db)):
    return sessions.get_session(conn, session_id)


def _sse(events):
    async def stream():
        try:
            async for event in events:
                payload = json.dumps(event["data"], ensure_ascii=False, default=str)
                yield f"event: {event['event']}\ndata: {payload}\n\n"
        except WeltmodellError as exc:
            # Fehler nach Response-Start (z. B. unbekannte Session) können
            # kein 4xx mehr werden — als error-Event ausliefern.
            payload = json.dumps({"message": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/messages")
def post_message(session_id: str, payload: MessageIn):
    turn = AgentTurn(session_id)
    return _sse(turn.run(user_text=payload.text, model=payload.model))


@router.post("/sessions/{session_id}/confirm")
def post_confirm(session_id: str, payload: ConfirmIn):
    turn = AgentTurn(session_id)
    return _sse(turn.run(confirm=payload.model_dump()))
