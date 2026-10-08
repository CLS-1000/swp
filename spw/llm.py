"""LLM seam. Runtime uses httpx only. The model only phrases and extracts; it never decides."""

from __future__ import annotations

import json
import os
import re
from typing import Protocol

import httpx

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

EXTRACT_SYSTEM = (
    "Extract diagnostic readings from the mechanic's message. Reply with JSON only, no prose: "
    '{"readings":[{"value":<number>,"unit":"V|mV|ohm|kohm|A|mA|kPa|psi|Hz|ms|null",'
    '"state":"KEY_OFF|KOEO|CRANKING|RUNNING|UNPLUGGED|null"}],"dtcs":["P0300"],"yes_no":true|false|null}. '
    "Only include what the message literally says. Never infer or compute a value."
)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def explain(self, system: str, user: str) -> str: ...

    def extract(self, text: str) -> dict: ...


class AnthropicHTTP:
    def __init__(self, model: str | None = None, api_key: str | None = None, timeout: float = 20.0):
        from spw.sop import DEFAULT_MODEL

        self.model = model or os.environ.get("SPW_CHAT_MODEL") or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _post(self, system: str, user: str, max_tokens: int) -> str:
        if not self.configured:
            raise LLMError("no ANTHROPIC_API_KEY")
        try:
            resp = httpx.post(
                API_URL,
                headers={"x-api-key": self.api_key, "anthropic-version": API_VERSION, "content-type": "application/json"},
                json={"model": self.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(str(exc)) from exc

    def explain(self, system: str, user: str) -> str:
        return self._post(system, user, 700)

    def extract(self, text: str) -> dict:
        raw = self._post(EXTRACT_SYSTEM, text, 300)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise LLMError("extractor returned no JSON")
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError("extractor returned bad JSON") from exc


def default_llm() -> LLM | None:
    client = AnthropicHTTP()
    return client if client.configured else None
