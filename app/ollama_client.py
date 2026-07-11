"""Small, dependency-light Ollama client with structured-output support."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ):
        self.model = model or os.getenv("OLLAMA_MODEL", "gemma3:4b")
        self.base_url = (
            base_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
        ).rstrip("/")
        self.timeout = timeout

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_output: bool = False,
        temperature: float = 0.0,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": temperature},
        }
        if json_output:
            payload["format"] = "json"
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        raw = self.chat(system, user, json_output=True)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OllamaError("Ollama returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise OllamaError("Ollama JSON response must be an object")
        return value
