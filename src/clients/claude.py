"""Unified async Claude (Anthropic) wrapper with dual-model support."""
from __future__ import annotations

import asyncio
import base64

import anthropic
import structlog

log = structlog.get_logger()

_MAX_RETRIES = 3
_BASE_DELAY = 2.0  # seconds


class ClaudeService:
    """Async wrapper around the Anthropic API.

    Provides a single ``complete()`` method used by both the market analyst
    (deep model) and the trading engine (fast model).
    """

    def __init__(self, api_key: str, model_deep: str, model_fast: str) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model_deep = model_deep
        self.model_fast = model_fast

    async def complete(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> str:
        """Send a completion request with retry on rate limits."""
        use_model = model or self.model_fast
        kwargs: dict = {
            "model": use_model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = await self.client.messages.create(**kwargs)
                return response.content[0].text
            except anthropic.RateLimitError as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY * (2 ** attempt)
                    log.warning(
                        "claude_rate_limited",
                        attempt=attempt + 1,
                        retry_in=delay,
                        model=use_model,
                    )
                    await asyncio.sleep(delay)
                else:
                    log.error("claude_rate_limit_exhausted", model=use_model)
            except anthropic.APIStatusError as exc:
                if exc.status_code == 529:  # overloaded
                    last_exc = exc
                    if attempt < _MAX_RETRIES:
                        delay = _BASE_DELAY * (2 ** attempt)
                        log.warning(
                            "claude_overloaded",
                            attempt=attempt + 1,
                            retry_in=delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                raise

        raise last_exc  # type: ignore[misc]

    async def complete_deep(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
    ) -> str:
        """Convenience: use the deep (Opus) model."""
        return await self.complete(
            messages, system=system, max_tokens=max_tokens,
            model=self.model_deep,
        )

    async def complete_fast(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 4096,
    ) -> str:
        """Convenience: use the fast (Sonnet) model."""
        return await self.complete(
            messages, system=system, max_tokens=max_tokens,
            model=self.model_fast,
        )

    async def vision(
        self,
        image_bytes: bytes,
        prompt: str,
        media_type: str = "image/jpeg",
        system: str = "",
        max_tokens: int = 4096,
    ) -> str:
        """Analyze an image using Claude Vision. Uses the deep model."""
        b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }]
        return await self.complete(
            messages, system=system, max_tokens=max_tokens,
            model=self.model_deep,
        )
