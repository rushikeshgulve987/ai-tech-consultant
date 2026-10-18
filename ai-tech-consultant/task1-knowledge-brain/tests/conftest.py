import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.config import Settings
from app.embeddings import HashEmbedder
from app.llm import LLMResult
from ingestion.crawler import FetchResult


class FakeLLM:
    """Answers from the first chunk id it sees, unless the evidence says 'UNKNOWN'."""

    def __init__(self):
        self.calls = []

    async def complete(self, *, system, user, model, max_tokens=1024, images=None):
        self.calls.append(user)
        if images:
            return LLMResult(json.dumps({"description": "Settings page with a Save button", "labels": ["Save"], "uncertain": False}), model)
        import re
        ids = re.findall(r'<chunk id="([^"]+)"', user)
        expert = [i for i in ids if i.startswith("expert:")]
        if "swallow" in user.lower() and not expert:  # simulate: docs do not cover this
            ids = []
        if expert:
            ids = expert + [i for i in ids if i not in expert]
        if not ids:
            return LLMResult(json.dumps({"answerable": False, "answer": "", "cited_ids": [], "uncertainty": ""}), model)
        return LLMResult(json.dumps({"answerable": True, "answer": "Do the documented steps.", "cited_ids": [ids[0], "invented:99"], "uncertainty": ""}), model)


class FakeFetcher:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.fetched: list[str] = []
        self.fail: set[str] = set()

    async def fetch(self, url):
        self.fetched.append(url)
        if url in self.fail:
            return FetchResult(url, 503, error="HTTP 503")
        if url not in self.pages:
            return FetchResult(url, 404, error="HTTP 404", permanent=True)
        content = self.pages[url]
        ct = "application/xml" if url.endswith(".xml") else "image/png" if url.endswith(".png") else "text/html"
        return FetchResult(url, 200, content.encode() if isinstance(content, str) else content, ct)


def sitemap(entries: dict[str, str | None]) -> str:
    body = "".join(f"<url><loc>{u}</loc>{f'<lastmod>{m}</lastmod>' if m else ''}</url>" for u, m in entries.items())
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'


def article(title: str, body: str, img: str = "") -> str:
    return f"<html><body><nav>MENU</nav><article><h1>{title}</h1>{body}{img}</article><footer>FOOT</footer></body></html>"


@pytest.fixture
def settings(tmp_path):
    base = Settings.from_env()
    from dataclasses import replace
    return replace(base, db_path=":memory:", embedder="hash", request_delay_s=0, enable_scheduler=False, min_evidence_score=0.2,
                   escalation_eval_file=str(tmp_path / "esc.jsonl"))
