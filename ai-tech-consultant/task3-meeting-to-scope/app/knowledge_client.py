"""Client for Task 1's HTTP API. Task 3 never imports Task 1 code."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

import httpx


@dataclass
class KbResult:
    kind: str                     # answered | insufficient | unavailable
    answer: str = ""
    sources: list[str] = field(default_factory=list)
    error: str = ""


class KnowledgeClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout_s: float = 30, retries: int = 2,
                 client: httpx.AsyncClient | None = None) -> None:
        self._url, self._key, self._retries = base_url.rstrip("/"), api_key, retries
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def query(self, question: str, context: str | None = None) -> KbResult:
        last = "unknown"
        for attempt in range(self._retries + 1):
            try:
                r = await self._client.post(f"{self._url}/query", json={"question": question, "context": context, "escalate_if_unanswered": True},
                                            headers={"X-Request-ID": uuid.uuid4().hex[:12], **({"X-API-Key": self._key} if self._key else {})})
                if r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                    await asyncio.sleep(0.3 * 2**attempt)
                    continue
                if r.status_code >= 400:
                    return KbResult("unavailable", error=f"HTTP {r.status_code}")
                d = r.json()
                if d["status"] == "answered":
                    return KbResult("answered", d.get("answer") or "", [s["url"] for s in d.get("sources", [])])
                return KbResult("insufficient")
            except (httpx.HTTPError, KeyError, ValueError) as err:
                last = f"{type(err).__name__}: {err}"
                await asyncio.sleep(0.3 * 2**attempt)
        return KbResult("unavailable", error=last)
