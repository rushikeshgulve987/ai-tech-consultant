"""Resumable, incremental ingestion. Every page's status is checkpointed in SQLite, so a crash resumes where it stopped."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, asdict

from app.config import Settings
from app.db import Database
from app.llm import LLM
from retrieval.chunker import chunk_article
from retrieval.store import Store, now_iso
from .crawler import PoliteFetcher
from .parser import parse_article
from .sitemap import SitemapEntry, diff_manifest, parse_sitemap
from .vision import describe_image

log = logging.getLogger("ingestion")


@dataclass
class RunSummary:
    run_id: int
    total: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    failed: int = 0
    unchanged: int = 0


async def load_sitemap(fetcher: PoliteFetcher, url: str, depth: int = 0) -> list[SitemapEntry]:
    res = await fetcher.fetch(url)
    if not res.ok:
        raise RuntimeError(f"sitemap fetch failed: {res.error}")
    entries, children = parse_sitemap(res.content.decode("utf-8", "replace"))
    if depth < 2:
        for child in children:
            entries.extend(await load_sitemap(fetcher, child, depth + 1))
    return list({e.url: e for e in entries}.values())


async def _process_page(entry: SitemapEntry, *, db: Database, store: Store, fetcher: PoliteFetcher, llm: LLM | None,
                        settings: Settings, summary: RunSummary, is_new: bool) -> None:
    row = db.one("SELECT content_hash, attempts FROM pages WHERE url=?", (entry.url,))
    attempts = (row["attempts"] if row else 0) + 1
    db.execute("INSERT INTO pages(url,lastmod,status,attempts,active,updated_at) VALUES(?,?,'pending',?,1,?) "
               "ON CONFLICT(url) DO UPDATE SET status='pending', attempts=?, active=1, updated_at=?",
               (entry.url, entry.lastmod, attempts, now_iso(), attempts, now_iso()))
    try:
        res = await fetcher.fetch(entry.url)
        if not res.ok:
            raise RuntimeError(res.error or "fetch failed")
        art = parse_article(entry.url, res.content.decode("utf-8", "replace"))
        if row and row["content_hash"] == art.content_hash:  # lastmod moved but content did not
            db.execute("UPDATE pages SET status='ok', lastmod=?, last_error=NULL, updated_at=? WHERE url=?",
                       (entry.lastmod, now_iso(), entry.url))
            summary.unchanged += 1
            return
        image_texts: dict[str, str] = {}
        if llm is not None:
            for ref in art.images[: settings.max_images_per_article]:
                cached = db.one("SELECT description,labels,uncertain FROM images WHERE image_url=?", (ref.url,))
                if cached:
                    from .vision import ImageDescription
                    d = ImageDescription(cached["description"], json.loads(cached["labels"]), bool(cached["uncertain"]))
                else:
                    d = await describe_image(llm, fetcher, ref, settings.vision_model, settings.max_image_bytes)
                    if d:
                        db.execute("INSERT OR REPLACE INTO images VALUES(?,?,?,?,?,?)",
                                   (ref.url, entry.url, d.description, json.dumps(d.labels), int(d.uncertain), now_iso()))
                if d:
                    image_texts[ref.url] = d.as_text(ref.alt)
        store.replace_article_chunks(entry.url, chunk_article(art, image_texts))
        db.execute("UPDATE pages SET status='ok', title=?, content_hash=?, lastmod=?, last_error=NULL, updated_at=? WHERE url=?",
                   (art.title, art.content_hash, entry.lastmod, now_iso(), entry.url))
        if is_new:
            summary.added += 1
        else:
            summary.updated += 1
    except Exception as err:  # failure isolation: one bad page never stops the run
        log.warning("page failed %s: %s", entry.url, err)
        db.execute("UPDATE pages SET status='failed', last_error=?, updated_at=? WHERE url=?", (str(err)[:500], now_iso(), entry.url))
        summary.failed += 1


async def run_ingestion(*, db: Database, store: Store, fetcher: PoliteFetcher, llm: LLM | None, settings: Settings,
                        limit: int | None = None) -> RunSummary:
    run_id = db.execute("INSERT INTO ingestion_runs(started_at,state) VALUES(?, 'running')", (now_iso(),)).lastrowid
    summary = RunSummary(run_id)
    try:
        current = await load_sitemap(fetcher, settings.sitemap_url)
        known = {r["url"]: dict(r) for r in db.query("SELECT url,lastmod,status,active,attempts FROM pages")}
        diff = diff_manifest(known, current)
        summary.total = len(current)
        for url in diff.removed:  # removed pages stop appearing in answers immediately
            store.deactivate_url(url)
            db.execute("UPDATE pages SET active=0, status='removed', updated_at=? WHERE url=?", (now_iso(), url))
            summary.removed += 1
        summary.unchanged = len(diff.unchanged)
        # Give up on pages that already failed max_attempts times; they show up in the failed count of /ingestion/status.
        todo = [(e, True) for e in diff.new if known.get(e.url, {}).get("attempts", 0) < settings.max_attempts]
        todo += [(e, False) for e in diff.changed]
        if limit:
            todo = todo[:limit]
        sem = asyncio.Semaphore(settings.max_concurrency)

        async def guarded(e: SitemapEntry, is_new: bool) -> None:
            async with sem:
                await _process_page(e, db=db, store=store, fetcher=fetcher, llm=llm, settings=settings, summary=summary, is_new=is_new)

        await asyncio.gather(*(guarded(e, n) for e, n in todo))
        state = "done"
    except Exception as err:
        log.exception("ingestion run failed")
        state = f"error: {err}"[:200]
    db.execute("UPDATE ingestion_runs SET finished_at=?, state=?, added=?, updated=?, removed=?, failed=?, unchanged=?, total=? WHERE id=?",
               (now_iso(), state, summary.added, summary.updated, summary.removed, summary.failed, summary.unchanged, summary.total, run_id))
    return summary


def status(db: Database) -> dict:
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM pages GROUP BY status")}
    last = db.one("SELECT * FROM ingestion_runs ORDER BY id DESC LIMIT 1")
    return {"pages": counts, "last_run": dict(last) if last else None}
