"""Chunk storage + hybrid retrieval (vector cosine + SQLite FTS5 BM25, merged with reciprocal rank fusion)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from app.db import Database
from app.embeddings import Embedder
from .chunker import Chunk


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Hit:
    id: str
    url: str
    kind: str
    heading: str
    text: str
    image_url: str | None
    cosine: float
    rrf: float


def fts_query(text: str) -> str | None:
    toks = [t for t in re.findall(r"[A-Za-z0-9]+", text.lower()) if len(t) > 2]
    return " OR ".join(f'"{t}"' for t in toks[:24]) or None


class Store:
    def __init__(self, db: Database, embedder: Embedder) -> None:
        self.db, self.embedder = db, embedder
        self._cache: tuple[list, np.ndarray] | None = None

    def _invalidate(self) -> None:
        self._cache = None

    def replace_article_chunks(self, url: str, chunks: list[Chunk]) -> None:
        """Atomic and idempotent: delete everything for this url, insert the new set."""
        vecs = self.embedder.embed([c.text for c in chunks]) if chunks else np.zeros((0, self.embedder.dim), np.float32)
        with self.db.tx():
            self._delete_url(url)
            for c, v in zip(chunks, vecs):
                self.db.execute("INSERT INTO chunks(id,url,kind,heading,text,image_url,embedding,active) VALUES(?,?,?,?,?,?,?,1)",
                                (c.id, c.url, c.kind, c.heading, c.text, c.image_url, v.astype(np.float32).tobytes()))
                self.db.execute("INSERT INTO chunks_fts(chunk_id,text,heading) VALUES(?,?,?)", (c.id, c.text, c.heading))
        self._invalidate()

    def _delete_url(self, url: str) -> None:
        ids = [r["id"] for r in self.db.query("SELECT id FROM chunks WHERE url=?", (url,))]
        for i in ids:
            self.db.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (i,))
        self.db.execute("DELETE FROM chunks WHERE url=?", (url,))

    def deactivate_url(self, url: str) -> None:
        """Removed pages must never appear in answers: drop their chunks from both indexes."""
        with self.db.tx():
            self._delete_url(url)
        self._invalidate()

    def add_expert_chunk(self, chunk_id: str, url: str, text: str, expires_at: str | None) -> None:
        vec = self.embedder.embed([text])[0]
        with self.db.tx():
            self.db.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (chunk_id,))
            self.db.execute("DELETE FROM chunks WHERE id=?", (chunk_id,))
            self.db.execute("INSERT INTO chunks(id,url,kind,heading,text,image_url,embedding,active,expires_at) "
                            "VALUES(?,?,?,?,?,NULL,?,1,?)", (chunk_id, url, "expert", "Expert answer", text,
                                                              vec.astype(np.float32).tobytes(), expires_at))
            self.db.execute("INSERT INTO chunks_fts(chunk_id,text,heading) VALUES(?,?,?)", (chunk_id, text, "Expert answer"))
        self._invalidate()

    def _matrix(self) -> tuple[list, np.ndarray]:
        if self._cache is None:
            rows = self.db.query("SELECT id,url,kind,heading,text,image_url,embedding,expires_at FROM chunks WHERE active=1")
            now = now_iso()
            rows = [r for r in rows if not r["expires_at"] or r["expires_at"] > now]
            mat = (np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
                   if rows else np.zeros((0, self.embedder.dim), np.float32))
            self._cache = (rows, mat)
        return self._cache

    def search(self, question: str, k: int = 6, pool: int = 20, mode: str = "hybrid") -> list[Hit]:
        """mode: hybrid | vector | keyword  (used by the retrieval ablation in evaluation)."""
        rows, mat = self._matrix()
        if not rows:
            return []
        qv = self.embedder.embed([question])[0]
        sims = mat @ qv
        by_id = {r["id"]: (i, r) for i, r in enumerate(rows)}
        vec_rank = [rows[i]["id"] for i in np.argsort(-sims)[:pool]] if mode != "keyword" else []
        kw_rank: list[str] = []
        q = fts_query(question)
        if q and mode != "vector":
            for r in self.db.query("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?", (q, pool)):
                if r["chunk_id"] in by_id:
                    kw_rank.append(r["chunk_id"])
        scores: dict[str, float] = {}
        for ranking in (vec_rank, kw_rank):
            for pos, cid in enumerate(ranking):
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + pos)
        hits = []
        for cid, s in sorted(scores.items(), key=lambda kv: -kv[1])[:k]:
            i, r = by_id[cid]
            hits.append(Hit(cid, r["url"], r["kind"], r["heading"], r["text"], r["image_url"], float(sims[i]), s))
        return hits
