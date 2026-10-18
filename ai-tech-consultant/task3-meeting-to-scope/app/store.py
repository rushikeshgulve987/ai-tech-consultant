"""Projects, meetings and versioned scope/proposal drafts. The approval gate lives here, not in the UI."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT, client TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS meetings(id INTEGER PRIMARY KEY, project_id INTEGER, title TEXT, meeting_date TEXT, transcript TEXT, constraints TEXT DEFAULT '[]', created_at TEXT);
CREATE TABLE IF NOT EXISTS versions(
  id INTEGER PRIMARY KEY, project_id INTEGER, version_no INTEGER, status TEXT,   -- draft | approved | superseded
  requirements TEXT, scope TEXT, estimate TEXT, proposal TEXT, change_report TEXT, issues TEXT, meeting_ids TEXT,
  created_at TEXT, approved_by TEXT, approved_at TEXT, sent_at TEXT, UNIQUE(project_id, version_no));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, project_id INTEGER, event TEXT, detail TEXT);
"""
now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731


class NotApproved(Exception):
    pass


class Store:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.c.executescript(SCHEMA)

    def _q(self, sql: str, p: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.c.execute(sql, p).fetchall()

    def _x(self, sql: str, p: tuple = ()) -> int:
        with self.lock:
            return self.c.execute(sql, p).lastrowid or 0

    def audit(self, project_id: int, event: str, detail: Any) -> None:
        self._x("INSERT INTO audit(ts,project_id,event,detail) VALUES(?,?,?,?)", (now(), project_id, event, json.dumps(detail)))

    def create_project(self, name: str, client: str) -> int:
        return self._x("INSERT INTO projects(name,client,created_at) VALUES(?,?,?)", (name, client, now()))

    def projects(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM projects ORDER BY id DESC")]

    def add_meeting(self, project_id: int, title: str, meeting_date: str, transcript: str) -> int:
        if not self._q("SELECT 1 FROM projects WHERE id=?", (project_id,)):
            raise KeyError("project")
        return self._x("INSERT INTO meetings(project_id,title,meeting_date,transcript,created_at) VALUES(?,?,?,?,?)", (project_id, title, meeting_date, transcript, now()))

    def meeting(self, mid: int) -> dict:
        r = self._q("SELECT * FROM meetings WHERE id=?", (mid,))
        if not r:
            raise KeyError("meeting")
        return dict(r[0])

    def meetings(self, project_id: int) -> list[dict]:
        return [dict(r) for r in self._q("SELECT id,title,meeting_date,created_at FROM meetings WHERE project_id=? ORDER BY id", (project_id,))]

    def set_constraints(self, mid: int, constraints: list[str]) -> None:
        self._x("UPDATE meetings SET constraints=? WHERE id=?", (json.dumps(constraints), mid))

    def baseline(self, project_id: int) -> dict | None:
        """The latest APPROVED version. Follow-ups are compared with this, never with an unapproved draft."""
        r = self._q("SELECT * FROM versions WHERE project_id=? AND status='approved' ORDER BY version_no DESC LIMIT 1", (project_id,))
        return self._decode(r[0]) if r else None

    def next_version_no(self, project_id: int) -> int:
        return (self._q("SELECT COALESCE(MAX(version_no),0) n FROM versions WHERE project_id=?", (project_id,))[0]["n"]) + 1

    def create_version(self, project_id: int, *, requirements: list, scope: dict, estimate: dict, proposal: dict, change_report: dict | None,
                       issues: list[str], meeting_ids: list[int]) -> int:
        no = self.next_version_no(project_id)
        vid = self._x("INSERT INTO versions(project_id,version_no,status,requirements,scope,estimate,proposal,change_report,issues,meeting_ids,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (project_id, no, "draft", json.dumps(requirements), json.dumps(scope), json.dumps(estimate), json.dumps(proposal),
                       json.dumps(change_report), json.dumps(issues), json.dumps(meeting_ids), now()))
        self.audit(project_id, "version_created", {"version_id": vid, "version_no": no})
        return vid

    def _decode(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("requirements", "scope", "estimate", "proposal", "change_report", "issues", "meeting_ids"):
            d[k] = json.loads(d[k]) if d[k] else None
        return d

    def version(self, vid: int) -> dict:
        r = self._q("SELECT * FROM versions WHERE id=?", (vid,))
        if not r:
            raise KeyError("version")
        return self._decode(r[0])

    def versions(self, project_id: int) -> list[dict]:
        return [{k: v for k, v in self._decode(r).items() if k in ("id", "version_no", "status", "approved_by", "approved_at", "sent_at", "created_at", "issues")}
                for r in self._q("SELECT * FROM versions WHERE project_id=? ORDER BY version_no DESC", (project_id,))]

    def approve(self, vid: int, reviewer: str, acknowledge_issues: bool = False) -> dict:
        v = self.version(vid)
        if v["status"] != "draft":
            raise ValueError(f"version is {v['status']}, only drafts can be approved")
        if not reviewer.strip():
            raise ValueError("reviewer name is required")
        if v["issues"] and not acknowledge_issues:
            raise ValueError("this draft has unresolved consistency issues; acknowledge them to approve")
        with self.lock:
            self.c.execute("BEGIN")
            try:
                self.c.execute("UPDATE versions SET status='superseded' WHERE project_id=? AND status='approved'", (v["project_id"],))
                self.c.execute("UPDATE versions SET status='approved', approved_by=?, approved_at=? WHERE id=?", (reviewer, now(), vid))
                self.c.execute("COMMIT")
            except BaseException:
                self.c.execute("ROLLBACK")
                raise
        self.audit(v["project_id"], "approved", {"version_id": vid, "reviewer": reviewer})
        return self.version(vid)

    def send(self, vid: int) -> dict:
        """Simulated delivery. Refuses anything that is not the approved version."""
        v = self.version(vid)
        if v["status"] != "approved":
            raise NotApproved(f"version {v['version_no']} is '{v['status']}': nothing reaches the client until a human approves it")
        if v["sent_at"]:
            return v
        self._x("UPDATE versions SET sent_at=? WHERE id=?", (now(), vid))
        self.audit(v["project_id"], "sent", {"version_id": vid})
        return self.version(vid)
