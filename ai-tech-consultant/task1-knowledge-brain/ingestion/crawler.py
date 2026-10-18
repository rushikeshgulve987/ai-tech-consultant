"""Polite fetcher: robots.txt, concurrency cap, delay between requests, retries with backoff."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urlparse

import httpx

TEMPORARY_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    status: int | None
    content: bytes = b""
    content_type: str = ""
    error: str | None = None
    permanent: bool = False  # True -> do not retry

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300


class PoliteFetcher:
    def __init__(self, user_agent: str, max_concurrency: int = 3, delay_s: float = 1.0, max_attempts: int = 4,
                 client: httpx.AsyncClient | None = None, respect_robots: bool = True) -> None:
        self._ua = user_agent
        self._sem = asyncio.Semaphore(max_concurrency)
        self._delay = delay_s
        self._max_attempts = max_attempts
        self._client = client or httpx.AsyncClient(headers={"User-Agent": user_agent}, timeout=30, follow_redirects=True)
        self._respect_robots = respect_robots
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_request = 0.0
        self._rate_lock = asyncio.Lock()

    async def _allowed(self, url: str) -> bool:
        if not self._respect_robots:
            return True
        origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if origin not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = await self._client.get(origin + "/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except httpx.HTTPError:
                rp.parse([])  # robots unreachable: treat as no restrictions, still rate-limited
            self._robots[origin] = rp
        rp = self._robots[origin]
        return rp.can_fetch(self._ua, url) if rp else True

    async def _pace(self) -> None:
        async with self._rate_lock:
            wait = self._last_request + self._delay - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def fetch(self, url: str) -> FetchResult:
        if not await self._allowed(url):
            return FetchResult(url, None, error="blocked by robots.txt", permanent=True)
        last = FetchResult(url, None, error="not attempted")
        for attempt in range(self._max_attempts):
            async with self._sem:
                await self._pace()
                try:
                    r = await self._client.get(url)
                except httpx.HTTPError as err:
                    last = FetchResult(url, None, error=f"{type(err).__name__}: {err}")
                else:
                    ct = r.headers.get("content-type", "")
                    if 200 <= r.status_code < 300:
                        return FetchResult(url, r.status_code, r.content, ct)
                    last = FetchResult(url, r.status_code, error=f"HTTP {r.status_code}",
                                       permanent=r.status_code not in TEMPORARY_STATUS)
                    if last.permanent:
                        return last
            await asyncio.sleep(min(30, 2**attempt + random.random()))  # exponential backoff + jitter
        return last

    async def aclose(self) -> None:
        await self._client.aclose()
