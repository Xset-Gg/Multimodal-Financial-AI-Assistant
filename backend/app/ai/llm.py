"""Minimal Groq chat-completions client (OpenAI-compatible API) for text reasoning."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class GroqLLM:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        settings = get_settings()
        if not settings.groq_api_key:
            raise LLMError("LLM disabled: GROQ_API_KEY is not configured")
        self._key = settings.groq_api_key.get_secret_value()
        self._model = settings.groq_text_model
        self._url = settings.groq_base_url.rstrip("/") + "/chat/completions"
        self._timeout = settings.groq_timeout_seconds
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def complete_json(self, messages: list[dict[str, Any]], max_tokens: int = 600) -> dict[str, Any]:
        body = {"model": self._model, "messages": messages, "temperature": 0, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"}}
        async with self._client() as client:
            try:
                resp = await client.post(self._url, json=body, headers={"Authorization": f"Bearer {self._key}"})
            except httpx.HTTPError as exc:
                raise LLMError(f"request failed ({type(exc).__name__})") from exc
        if resp.status_code != 200:
            log.warning("groq text model returned HTTP %s", resp.status_code)
            raise LLMError(f"HTTP {resp.status_code}")
        try:
            data = json.loads(resp.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMError("unreadable response") from exc
        if not isinstance(data, dict):
            raise LLMError("unexpected response structure")
        return data

    async def stream_text(self, messages: list[dict[str, Any]], max_tokens: int = 400) -> AsyncIterator[str]:
        body = {"model": self._model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens,
                "stream": True}
        async with self._client() as client:
            try:
                async with client.stream("POST", self._url, json=body,
                                         headers={"Authorization": f"Bearer {self._key}"}) as resp:
                    if resp.status_code != 200:
                        log.warning("groq text model returned HTTP %s", resp.status_code)
                        raise LLMError(f"HTTP {resp.status_code}")
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            delta = json.loads(payload)["choices"][0]["delta"].get("content")
                        except (KeyError, IndexError, ValueError, TypeError):
                            continue
                        if delta:
                            yield delta
            except httpx.HTTPError as exc:
                raise LLMError(f"stream failed ({type(exc).__name__})") from exc
