"""OpenAI-compatible chat completions client via httpx (no new deps)."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LLMClient:
    """Thin wrapper around the OpenAI chat completions API.

    Uses httpx directly to avoid adding openai as a dependency.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError(
                "OpenAI API key is required. Set OPENAI_API_KEY environment variable."
            )
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 100,
        temperature: float = 0.0,
    ) -> str:
        """Single chat completion. Returns the content string."""
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        resp = self._client.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    # Match CORRECT or WRONG as the first word in the response
    _VERDICT_RE = re.compile(r"^\s*(CORRECT|WRONG)\b", re.IGNORECASE)

    def judge(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 200,
    ) -> tuple[bool, str]:
        """Judge call. Returns (correct, explanation).

        Expects the LLM to respond with CORRECT or WRONG as the first word,
        optionally followed by an explanation.
        """
        content = self.chat(model, messages, max_tokens=max_tokens, temperature=0.0)
        match = self._VERDICT_RE.match(content)
        if match:
            correct = match.group(1).upper() == "CORRECT"
        else:
            # Fallback: check for YES/NO pattern
            upper = content.upper().strip()
            correct = upper.startswith("YES") or upper.startswith("CORRECT")
        return correct, content

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def __enter__(self) -> LLMClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
