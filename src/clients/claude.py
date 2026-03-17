"""Unified async Claude (Anthropic) wrapper with dual-model support."""
from __future__ import annotations

import anthropic
import structlog

log = structlog.get_logger()


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
        """Send a completion request. Defaults to the fast model."""
        use_model = model or self.model_fast
        kwargs: dict = {
            "model": use_model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = await self.client.messages.create(**kwargs)
        return response.content[0].text

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
