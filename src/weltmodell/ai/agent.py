"""Agent-Loop: Nachricht rein → Modell antwortet mit Text oder Tool-Calls →
Tools ausführen → Ergebnisse zurück → bis zur finalen Antwort.

Max. 20 LLM-Iterationen pro Turn, dann sauberer Abbruch mit Zwischenstand.
Alles wird als Event-Stream geliefert (der Router serialisiert zu SSE):
Text-Tokens, Tool-Call-Start, Tool-Ergebnis-Digest, Bestätigungsanfragen
(Schreib-Gate), Fehler.
"""

import json
import time
from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import anyio

from ..db import get_conn
from ..errors import WeltmodellError
from . import compute, providers, results, sessions, tools
from .prompt import build_system_prompt

MAX_ITERATIONS = 20
# Deckel fürs UI-Event (nicht fürs Modell): riesige Ergebnisse würden den
# SSE-Stream und den Browser fluten.
MAX_DISPLAY_CHARS = 60_000

REJECTION_MESSAGE = (
    "Der Nutzer hat diesen Schreib-Tool-Aufruf ABGELEHNT. Nicht erneut "
    "versuchen; frage nach, was stattdessen gewünscht ist."
)


async def _db(fn):
    """Kurze Persistenz-Transaktion im Thread (Event-Loop nicht blockieren)."""

    def run():
        with get_conn() as conn:
            return fn(conn)

    return await anyio.to_thread.run_sync(run)


def _open_tool_calls(messages: list[dict]) -> list[dict]:
    """Tool-Calls der letzten Assistant-Message, für die noch kein
    tool-Result in der History steht (Resume nach Gate/Neustart)."""
    last_calls: list[dict] = []
    answered: set[str] = set()
    for m in messages:
        payload = m["payload"]
        if payload.get("role") == "assistant" and payload.get("tool_calls"):
            last_calls = payload["tool_calls"]
            answered = set()
        elif payload.get("role") == "tool":
            answered.add(payload.get("tool_call_id"))
    return [c for c in last_calls if c["id"] not in answered]


def _extract_anchors(name: str, arguments: dict, result: Any) -> list[dict]:
    """Erfolgreich aufgelöste Entities für den Anker-Cache einsammeln."""
    if not isinstance(result, dict):
        return []
    if name == "welt_resolve":
        candidates = result.get("candidates") or []
        if result.get("match"):
            label = arguments.get("label")
            for c in candidates:
                if str(c.get("id")) == str(result["match"]):
                    label = c.get("label") or label
            return [{
                "id": str(result["match"]),
                "label": label,
                "type_id": arguments.get("type_id"),
            }]
        # Kein deterministischer Match: ein EINDEUTIGER exakter Label-Treffer
        # (similarity 1.0) ist als Anker sicher genug — bei mehreren
        # Exakt-Treffern (Dubletten) lieber keiner.
        exact = [c for c in candidates if c.get("similarity") == 1.0]
        if len(exact) == 1:
            return [{
                "id": str(exact[0]["id"]),
                "label": exact[0].get("label"),
                "type_id": exact[0].get("type_id") or arguments.get("type_id"),
            }]
    if name in ("welt_create_entity", "welt_create_entities"):
        created = result if name == "welt_create_entity" else None
        items = [created] if created else result.get("entities") or []
        return [
            {"id": str(e["id"]), "label": e.get("label"), "type_id": e.get("type_id")}
            for e in items
            if isinstance(e, dict) and e.get("id")
        ]
    return []


def _anchors_block(anchors: list[dict]) -> str:
    lines = "\n".join(
        f"- {a.get('label') or '(ohne Label)'} ({a.get('type_id') or '?'}): {a['id']}"
        for a in anchors
    )
    return (
        "[Anker-Cache — bereits aufgelöste Entities, nicht erneut auflösen]\n"
        + lines
    )


def _display(value: Any) -> Any:
    """Ergebnis fürs UI-Event, hart gedeckelt."""
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if len(serialized) <= MAX_DISPLAY_CHARS:
        return value
    return {"_truncated": True, "_preview": serialized[:MAX_DISPLAY_CHARS]}


class AgentTurn:
    """Ein Turn: User-Nachricht ODER Gate-Entscheidung rein, Events raus."""

    def __init__(self, session_id: str):
        self.session_id = session_id

    # --- Tool-Ausführung -------------------------------------------------------

    async def _execute_tool(self, call: dict) -> tuple[dict, dict]:
        """(tool-Message fürs LLM, tool_result-Event fürs UI)."""
        call_id = call["id"]
        name = call["function"]["name"]
        started = time.monotonic()
        try:
            arguments = json.loads(call["function"]["arguments"] or "{}")
            if not isinstance(arguments, dict):
                raise WeltmodellError("Tool-Argumente müssen ein Objekt sein")
        except (json.JSONDecodeError, WeltmodellError) as exc:
            return self._tool_error(call, f"Ungültige Tool-Argumente: {exc}", started)

        try:
            if name == "compute":
                raw = await self._run_compute(arguments)
            else:
                raw = await tools.execute_tool(name, arguments, self.session_id)
        except (tools.ToolError, WeltmodellError) as exc:
            return self._tool_error(call, str(exc), started)

        model_result, offloaded = await _db(
            partial(results.offload_if_large, session_id=self.session_id,
                    tool_call_id=call_id, value=raw)
        )
        anchors = _extract_anchors(name, arguments, raw)
        if anchors:
            await _db(partial(sessions.add_anchors, session_id=self.session_id,
                              anchors=anchors))
        duration_ms = int((time.monotonic() - started) * 1000)
        message = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(model_result, ensure_ascii=False, default=str),
            "_ui": {"name": name, "duration_ms": duration_ms,
                    "offloaded": offloaded},
        }
        event = {
            "event": "tool_result",
            "data": {
                "id": call_id, "name": name, "duration_ms": duration_ms,
                "offloaded": offloaded,
                "ref": model_result.get("ref") if offloaded else None,
                "display": _display(model_result if offloaded else raw),
            },
        }
        return message, event

    def _tool_error(self, call: dict, error: str, started: float) -> tuple[dict, dict]:
        duration_ms = int((time.monotonic() - started) * 1000)
        name = call["function"]["name"]
        message = {
            "role": "tool",
            "tool_call_id": call["id"],
            "content": json.dumps({"error": error}, ensure_ascii=False),
            "_ui": {"name": name, "duration_ms": duration_ms, "error": error},
        }
        event = {
            "event": "tool_result",
            "data": {"id": call["id"], "name": name, "duration_ms": duration_ms,
                     "error": error, "display": {"error": error}},
        }
        return message, event

    async def _run_compute(self, arguments: dict) -> Any:
        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip():
            raise WeltmodellError("compute braucht einen nicht-leeren code-String")
        refs_spec = arguments.get("refs") or {}
        if not isinstance(refs_spec, dict):
            raise WeltmodellError("refs muss ein Objekt {name: 'ref:<id>'} sein")

        def resolve_all(conn):
            return {
                name: results.resolve_ref(conn, self.session_id, ref)
                for name, ref in refs_spec.items()
            }

        resolved = await _db(resolve_all)
        return await anyio.to_thread.run_sync(
            partial(compute.run_compute, code, resolved)
        )

    # --- Turn-Ablauf -------------------------------------------------------------

    async def run(
        self,
        user_text: str | None = None,
        confirm: dict | None = None,
        model: str | None = None,
    ) -> AsyncIterator[dict]:
        session = await _db(partial(sessions.get_session, session_id=self.session_id))
        if model and model != session["model"]:
            await _db(partial(sessions.touch, session_id=self.session_id, model=model))
            session["model"] = model

        # 1) Gate-Entscheidung einarbeiten (pending → tool-Result).
        pending = session.get("pending")
        if confirm is not None:
            if not pending or pending["tool_call_id"] != confirm.get("tool_call_id"):
                yield {"event": "error",
                       "data": {"message": "Keine passende Bestätigung offen."}}
                return
            async for event in self._settle_pending(pending, bool(confirm.get("approved"))):
                yield event
        elif pending and user_text is not None:
            # Neue Nachricht bei offenem Gate = implizite Ablehnung — die
            # History braucht ein tool-Result pro tool_call.
            async for event in self._settle_pending(pending, False):
                yield event

        # 2) Neue User-Nachricht (+ Anker-Delta-Block davor, append-only).
        if user_text is not None:
            fresh = await _db(partial(sessions.unsent_anchors,
                                      session_id=self.session_id))
            if fresh:
                await _db(partial(
                    sessions.append_message, session_id=self.session_id,
                    payload={"role": "user", "content": _anchors_block(fresh),
                             "_ui": {"kind": "anchors", "anchors": fresh}},
                ))
                await _db(partial(sessions.mark_anchors_sent,
                                  session_id=self.session_id))
            await _db(partial(sessions.append_message, session_id=self.session_id,
                              payload={"role": "user", "content": user_text}))
            if not session["title"]:
                await _db(partial(sessions.touch, session_id=self.session_id,
                                  title=user_text[:80]))

        # 3) Loop.
        try:
            provider = providers.get_provider()
        except providers.ProviderError as exc:
            yield {"event": "error", "data": {"message": str(exc)}}
            return
        model_id = session["model"] or providers.get_model_id()
        system_prompt = await anyio.to_thread.run_sync(build_system_prompt)
        tool_schemas = tools.llm_tool_schemas() + [compute.COMPUTE_TOOL_SCHEMA]

        for iteration in range(MAX_ITERATIONS):
            stored = await _db(partial(sessions.get_session,
                                       session_id=self.session_id))

            # 3a) Offene Tool-Calls abarbeiten (auch Resume nach Gate).
            open_calls = _open_tool_calls(stored["messages"])
            paused = False
            for call in open_calls:
                name = call["function"]["name"]
                if tools.is_write_tool(name):
                    arguments = _safe_parse(call["function"]["arguments"])
                    pending = {"tool_call_id": call["id"], "name": name,
                               "arguments": arguments}
                    await _db(partial(sessions.touch, session_id=self.session_id,
                                      pending=pending))
                    yield {"event": "confirm_required", "data": pending}
                    yield {"event": "done", "data": {"reason": "confirm"}}
                    return
                yield {"event": "tool_start",
                       "data": {"id": call["id"], "name": name,
                                "arguments": _safe_parse(call["function"]["arguments"])}}
                message, event = await self._execute_tool(call)
                await _db(partial(sessions.append_message,
                                  session_id=self.session_id, payload=message))
                yield event
            if open_calls:
                stored = await _db(partial(sessions.get_session,
                                           session_id=self.session_id))

            # 3b) LLM-Runde.
            history = sessions.llm_messages(stored["messages"])
            messages = [{"role": "system", "content": system_prompt}, *history]
            assistant: dict[str, Any] | None = None
            try:
                async for chunk in provider.stream_chat(messages, tool_schemas,
                                                        model_id):
                    if chunk["type"] == "text":
                        yield {"event": "token", "data": {"text": chunk["delta"]}}
                    elif chunk["type"] == "message":
                        assistant = chunk["message"]
            except Exception as exc:
                yield {"event": "error", "data": {"message": f"LLM-Fehler: {exc}"}}
                return
            if assistant is None:
                yield {"event": "error",
                       "data": {"message": "LLM lieferte keine Antwort."}}
                return

            await _db(partial(sessions.append_message, session_id=self.session_id,
                              payload=assistant))
            await _db(partial(sessions.touch, session_id=self.session_id))
            yield {"event": "assistant",
                   "data": {"content": assistant.get("content"),
                            "tool_calls": [
                                {"id": c["id"], "name": c["function"]["name"],
                                 "arguments": _safe_parse(c["function"]["arguments"])}
                                for c in assistant.get("tool_calls") or []
                            ]}}

            if not assistant.get("tool_calls"):
                yield {"event": "done", "data": {"reason": "final"}}
                return

        # 4) Limit erreicht: Zwischenstand zusammenfassen (ohne Tools).
        note = {"role": "user",
                "content": "[System] Iterationslimit erreicht. Fasse den "
                           "Zwischenstand und offene Schritte kurz zusammen.",
                "_ui": {"kind": "system-note"}}
        await _db(partial(sessions.append_message, session_id=self.session_id,
                          payload=note))
        stored = await _db(partial(sessions.get_session, session_id=self.session_id))
        messages = [{"role": "system", "content": system_prompt},
                    *sessions.llm_messages(stored["messages"])]
        try:
            async for chunk in provider.stream_chat(messages, [], model_id):
                if chunk["type"] == "text":
                    yield {"event": "token", "data": {"text": chunk["delta"]}}
                elif chunk["type"] == "message":
                    await _db(partial(sessions.append_message,
                                      session_id=self.session_id,
                                      payload=chunk["message"]))
                    yield {"event": "assistant",
                           "data": {"content": chunk["message"].get("content"),
                                    "tool_calls": []}}
        except Exception as exc:
            yield {"event": "error", "data": {"message": f"LLM-Fehler: {exc}"}}
            return
        yield {"event": "done", "data": {"reason": "max_iterations"}}

    async def _settle_pending(self, pending: dict, approved: bool) -> AsyncIterator[dict]:
        call = {"id": pending["tool_call_id"],
                "function": {"name": pending["name"],
                             "arguments": json.dumps(pending["arguments"],
                                                     ensure_ascii=False)}}
        if approved:
            yield {"event": "tool_start",
                   "data": {"id": call["id"], "name": pending["name"],
                            "arguments": pending["arguments"], "approved": True}}
            message, event = await self._execute_tool(call)
            await _db(partial(sessions.append_message, session_id=self.session_id,
                              payload=message))
            yield event
        else:
            message = {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps({"rejected": REJECTION_MESSAGE},
                                      ensure_ascii=False),
                "_ui": {"name": pending["name"], "rejected": True},
            }
            await _db(partial(sessions.append_message, session_id=self.session_id,
                              payload=message))
            yield {"event": "tool_result",
                   "data": {"id": call["id"], "name": pending["name"],
                            "rejected": True, "display": {"rejected": True}}}
        await _db(partial(sessions.touch, session_id=self.session_id, pending=None))


def _safe_parse(raw: str) -> Any:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}
