"""Thin Claude wrapper: retries, JSON parsing, token usage for cost tracking."""
from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Settings


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


@dataclass
class UsageTracker:
    prices: dict[str, tuple[float, float]]
    calls: list[LLMResult] = field(default_factory=list)

    def add(self, r: LLMResult) -> None:
        self.calls.append(r)

    def cost_usd(self) -> float | None:
        if not self.prices:
            return None
        total = 0.0
        for c in self.calls:
            p = self.prices.get(c.model)
            if p is None:
                return None
            total += c.input_tokens / 1e6 * p[0] + c.output_tokens / 1e6 * p[1]
        return total


class LLM(Protocol):
    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 1024,
                       images: list[tuple[bytes, str]] | None = None) -> LLMResult: ...


def extract_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


async def complete_json(llm: LLM, *, system: str, user: str, model: str, max_tokens: int = 1024,
                        images: list[tuple[bytes, str]] | None = None) -> tuple[dict[str, Any], LLMResult]:
    """Ask for JSON; on a parse failure retry once, telling the model what went wrong."""
    r = await llm.complete(system=system, user=user, model=model, max_tokens=max_tokens, images=images)
    try:
        return extract_json(r.text), r
    except ValueError as err:
        r2 = await llm.complete(system=system, user=f"{user}\n\nYour previous reply was not valid JSON ({err}). "
                                "Reply with ONLY one JSON object.", model=model, max_tokens=max_tokens, images=images)
        return extract_json(r2.text), r2


class AnthropicLLM:
    def __init__(self, settings: Settings, tracker: UsageTracker | None = None) -> None:
        from anthropic import AsyncAnthropic  # imported lazily so tests need no key

        self._client = AsyncAnthropic()
        self.tracker = tracker or UsageTracker(settings.model_prices)

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 1024,
                       images: list[tuple[bytes, str]] | None = None) -> LLMResult:
        content: list[dict[str, Any]] = []
        for data, media_type in images or []:
            content.append({"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                         "data": base64.b64encode(data).decode()}})
        content.append({"type": "text", "text": user})
        last: Exception | None = None
        for attempt in range(4):
            t0 = time.perf_counter()
            try:
                msg = await self._client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                                         messages=[{"role": "user", "content": content}])
                text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
                res = LLMResult(text, model, msg.usage.input_tokens, msg.usage.output_tokens, time.perf_counter() - t0)
                self.tracker.add(res)
                return res
            except Exception as err:  # network, 429, 5xx: back off and retry
                last = err
                await asyncio.sleep(min(20, 2**attempt + random.random()))
        raise RuntimeError(f"LLM call failed after retries: {last}")
