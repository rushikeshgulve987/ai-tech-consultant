import asyncio

from fastapi.testclient import TestClient

from app.main import create_app
from app.pipeline import Deps
from app.store import Store
from tests.conftest import A, C, FakeKB, FakeLLM


def make(settings, kb=None):
    return Deps(settings, FakeLLM(), kb or FakeKB(), Store(":memory:"))


def test_end_to_end_initial_then_followup(settings):
    d = make(settings)
    c = TestClient(create_app(d))
    pid = c.post("/projects", json={"name": "Directory search", "client": "Priya"}).json()["id"]
    m1 = c.post(f"/projects/{pid}/meetings", json={"title": "A", "meeting_date": "2026-10-01", "transcript": A}).json()["id"]
    v1 = c.post(f"/projects/{pid}/meetings/{m1}/process").json()["version_id"]
    ver1 = c.get(f"/versions/{v1}").json()
    assert ver1["status"] == "draft" and len(ver1["requirements"]) == 4
    assert all(e["verified"] for r in ver1["requirements"] for e in r["evidence"])          # quotes really exist in transcript A
    assert "REQ-004: AI search" not in ver1["scope"]["agreed_requirements"]                  # unclear item kept out of agreed scope
    assert all(t["requirement_id"] != "REQ-999" for t in ver1["estimate"]["tasks"])           # ghost task dropped
    assert c.post(f"/versions/{v1}/send").status_code == 403                                  # approval gate
    assert c.post(f"/versions/{v1}/approve", json={"reviewer": "Ann", "acknowledge_issues": True}).status_code == 200
    assert c.post(f"/versions/{v1}/send").status_code == 200

    m2 = c.post(f"/projects/{pid}/meetings", json={"title": "C", "meeting_date": "2026-10-10", "transcript": C}).json()["id"]
    v2 = c.post(f"/projects/{pid}/meetings/{m2}/process").json()["version_id"]
    ver2 = c.get(f"/versions/{v2}").json()
    items = {(i["requirement_id"], i["kind"]) for i in ver2["change_report"]["items"]}
    assert ("REQ-002", "modified") in items and ("REQ-004", "removed") in items
    assert any(k == "added" for _, k in items) and ("REQ-003", "unchanged") in items
    assert ver2["change_report"]["baseline_version"] == 1
    assert ver2["version_no"] == 2 and ver2["status"] == "draft"
    assert c.post(f"/versions/{v2}/send").status_code == 403                                  # revision is unapproved: cannot reach the client
    unchanged_tasks = [t for t in ver2["estimate"]["tasks"] if t["requirement_id"] == "REQ-001"]
    assert len(unchanged_tasks) == 1                                                          # unchanged work carried over, not re-estimated
    assert any("5th of December" in x for x in ver2["change_report"]["date_or_budget_changes"])


def test_kb_unavailable_marks_needs_clarification_not_custom_work(settings):
    d = make(settings, FakeKB("unavailable"))
    c = TestClient(create_app(d))
    pid = c.post("/projects", json={"name": "p", "client": "c"}).json()["id"]
    m = c.post(f"/projects/{pid}/meetings", json={"title": "A", "meeting_date": "2026-10-01", "transcript": A}).json()["id"]
    v = c.get(f"/versions/{c.post(f'/projects/{pid}/meetings/{m}/process').json()['version_id']}").json()
    assert all(r["feasibility"] == "needs_clarification" for r in v["requirements"])
    assert any("unverified product capability" in i for i in v["issues"])


def test_api_validation(settings):
    c = TestClient(create_app(make(settings)))
    assert c.post("/projects", json={"name": "", "client": "x"}).status_code == 422
    assert c.post("/projects/99/meetings", json={"title": "t", "meeting_date": "2026-01-01", "transcript": "x" * 30}).status_code == 404
    assert c.post("/projects/1/meetings", json={"title": "t", "meeting_date": "01/01/2026", "transcript": "x" * 30}).status_code == 422
