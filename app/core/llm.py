"""
Thin wrapper around the Groq chat completions API, with retry logic and
a consistent interface so `graph_rag.py` doesn't need to know about the
Groq SDK directly.
"""

from __future__ import annotations

import logging

try:
    from groq import Groq
except ModuleNotFoundError:  # pragma: no cover - exercised in lightweight environments
    Groq = None  # type: ignore[assignment]
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings

logger = logging.getLogger("app.llm")

_client: Groq | None = None


def get_groq_client() -> Groq:
    global _client
    if _client is None:
        if Groq is None:
            raise ImportError("The 'groq' package is required to call the LLM.")
        settings = get_settings()
        _client = Groq(api_key=settings.groq_api_key)
    return _client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4))
def chat_completion(
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> str:
    """Single-turn chat completion. Retries on transient Groq/network errors."""
    client = get_groq_client()
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        # 413 = payload too large — retrying won't help; surface immediately
        if getattr(exc, "status_code", None) == 413:
            raise ContextTooLargeError(str(exc)) from exc
        raise
    content = response.choices[0].message.content or ""
    return content.strip()


class ContextTooLargeError(RuntimeError):
    """Raised when the assembled context exceeds the model's token limit."""
