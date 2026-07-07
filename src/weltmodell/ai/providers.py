"""Modell-Layer: Provider-Abstraktion über das OpenAI-kompatible
Chat-Completions-Format mit Tool-Calling (Streaming via SSE).

Providerspezifisches (URL-Aufbau, Auth-Header) lebt NUR hier. Auswahl per
Env MODEL_PROVIDER (openrouter | azure) und MODEL_ID; das UI kann das
Modell pro Chat überschreiben.
"""

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..errors import WeltmodellError

DEFAULT_MODEL = "poolside/laguna-xs-2.1:free"


class ProviderError(WeltmodellError):
    """LLM-Aufruf fehlgeschlagen (Netz, Auth, unerwartete Antwort)."""


def get_model_id() -> str:
    return os.environ.get("MODEL_ID") or os.environ.get(
        "WELTMODELL_LLM_MODEL", DEFAULT_MODEL
    )


def get_ui_models() -> list[str]:
    """Modelle fürs UI-Dropdown: WORLDAI_MODELS (Kommaliste) ∪ Default."""
    raw = os.environ.get("WORLDAI_MODELS", "")
    models = [m.strip() for m in raw.split(",") if m.strip()]
    default = get_model_id()
    if default not in models:
        models.insert(0, default)
    return models


class ChatProvider:
    """OpenAI-kompatibler Streaming-Client. Subklassen liefern URL + Header."""

    timeout = 300.0

    def _url(self, model: str) -> str:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _body(self, model: str, messages: list[dict], tools: list[dict]) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        return body

    async def stream_chat(
        self, messages: list[dict], tools: list[dict], model: str
    ) -> AsyncIterator[dict[str, Any]]:
        """Streamt {"type": "text", "delta": str}-Events und schließt mit
        {"type": "message", "message": <assistant-message>, "finish_reason": str}.
        Tool-Call-Deltas werden hier akkumuliert — der Agent-Loop sieht nur
        fertige tool_calls."""
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", self._url(model), headers=self._headers(),
                json=self._body(model, messages, tools),
            ) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode(errors="replace")
                    raise ProviderError(
                        f"LLM-Provider antwortet {response.status_code}: {detail[:500]}"
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        # OpenRouter streamt u. a. leere Keep-Alive-Chunks.
                        continue
                    choice = choices[0]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                        yield {"type": "text", "delta": delta["content"]}
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tool_calls.setdefault(
                            idx,
                            {"id": "", "type": "function",
                             "function": {"name": "", "arguments": ""}},
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]

        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
        }
        if tool_calls:
            message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
        yield {"type": "message", "message": message, "finish_reason": finish_reason}


class OpenRouterProvider(ChatProvider):
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self) -> None:
        self.api_key = os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ProviderError("OPENROUTER_API_KEY fehlt (.env oder Umgebung)")

    def _url(self, model: str) -> str:
        return self.BASE_URL

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


class AzureOpenAIProvider(ChatProvider):
    def __init__(self) -> None:
        self.endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        self.api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        self.api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
        if not self.endpoint or not self.api_key:
            raise ProviderError(
                "AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY fehlen"
            )

    def _url(self, model: str) -> str:
        # Bei Azure ist MODEL_ID der Deployment-Name.
        return (
            f"{self.endpoint}/openai/deployments/{model}"
            f"/chat/completions?api-version={self.api_version}"
        )

    def _headers(self) -> dict[str, str]:
        return {"api-key": self.api_key}

    def _body(self, model: str, messages: list[dict], tools: list[dict]) -> dict:
        body = super()._body(model, messages, tools)
        body.pop("model", None)  # steckt bei Azure in der URL
        return body


def get_provider() -> ChatProvider:
    name = os.environ.get("MODEL_PROVIDER", "openrouter").lower()
    if name == "openrouter":
        return OpenRouterProvider()
    if name == "azure":
        return AzureOpenAIProvider()
    raise ProviderError(f"Unbekannter MODEL_PROVIDER '{name}' (openrouter | azure)")
