"""Tiny SQLite wrapper. One connection guarded by a lock; fine for this scale."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages(
  url TEXT PRIMARY KEY, lastmod TEXT, content_hash TEXT, title TEXT,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | ok | failed | removed
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  active INTEGER NOT NULL DEFAULT 1, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks(
  id TEXT PRIMARY KEY, url TEXT NOT NULL, kind TEXT NOT NULL,   -- text | image | expert
  heading TEXT, text TEXT NOT NULL, image_url TEXT, embedding BLOB NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, expires_at TEXT
);
CREATE INDEX IF NOT EXISTS chunks_url ON chunks(url);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, text, heading);
CREATE TABLE IF NOT EXISTS images(
  image_url TEXT PRIMARY KEY, article_url TEXT, description TEXT, labels TEXT, uncertain INTEGER, described_at TEXT
);
CREATE TABLE IF NOT EXISTS escalations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL, norm_question TEXT NOT NULL,
  reason TEXT, evidence TEXT, status TEXT NOT NULL DEFAULT 'open',  -- open | resolved
  created_at TEXT, resolved_at TEXT, answer TEXT, resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS expert_knowledge(
  id INTEGER PRIMARY KEY AUTOINCREMENT, escalation_id INTEGER, version INTEGER, question TEXT, answer TEXT,
  scope_note TEXT, reviewed INTEGER, expires_at TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS ingestion_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, state TEXT,
  added INTEGER DEFAULT 0, updated INTEGER DEFAULT 0, removed INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
  unchanged INTEGER DEFAULT 0, total INTEGER DEFAULT 0
);
"""


class Database:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def tx(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None
