import asyncio

from fastapi.testclient import TestClient

from app.embeddings import HashEmbedder
from app.main import Deps, create_app
from ingestion.pipeline import run_ingestion
from tests.conftest import FakeFetcher, FakeLLM, article, sitemap

SM = "https://h.com/sitemap.xml"
A, B = "https://h.com/refunds", "https://h.com/logo"


def build(settings, pages):
    f = FakeFetcher(pages)
    deps = Deps(settings, llm=FakeLLM(), embedder=HashEmbedder(), fetcher=f)
    return deps, f


def pages_v1():
    return {
        SM: sitemap({A: "1", B: "1"}),
        A: article("Refunds", "<h2>Issue a refund</h2><p>Open Payments then click Refund to refund a member payment.</p><img src='https://h.com/i.png'>"),
        B: article("Upload a logo", "<p>Go to Design then Logo to upload your logo image.</p>"),
        "https://h.com/i.png": b"\x89PNG",
    }


def run(deps, **kw):
    s = deps.settings
    from dataclasses import replace
    deps.settings = replace(s, sitemap_url=SM)
    return asyncio.run(run_ingestion(db=deps.db, store=deps.store, fetcher=deps.fetcher, llm=deps.llm, settings=deps.settings, **kw))


def test_ingest_is_idempotent_and_incremental(settings):
    deps, f = build(settings, pages_v1())
    s1 = run(deps)
    assert (s1.added, s1.failed) == (2, 0)
    n = deps.db.one("SELECT COUNT(*) n FROM chunks")["n"]
    assert any(r["kind"] == "image" for r in deps.db.query("SELECT kind FROM chunks"))  # screenshot is searchable evidence
    f.fetched.clear()
    s2 = run(deps)
    assert s2.unchanged == 2 and s2.added == 0 and deps.db.one("SELECT COUNT(*) n FROM chunks")["n"] == n
    assert A not in f.fetched  # unchanged pages are not re-fetched


def test_failed_page_is_isolated_then_resumed(settings):
    deps, f = build(settings, pages_v1())
    f.fail.add(A)
    s1 = run(deps)
    assert s1.failed == 1 and s1.added == 1
    f.fail.clear()
    s2 = run(deps)
    assert s2.added == 1 and s2.failed == 0


def test_removed_article_stops_appearing_in_answers(settings):
    deps, f = build(settings, pages_v1())
    run(deps)
    c = TestClient(create_app(deps))
    r = c.post("/query", json={"question": "How do I refund a member payment?"}).json()
    assert r["status"] == "answered" and r["sources"][0]["url"] == A
    assert all(s["url"] != "invented:99" for s in r["sources"])  # invented citation ids are discarded
    f.pages[SM] = sitemap({B: "1"})
    s = run(deps)
    assert s.removed == 1
    r2 = c.post("/query", json={"question": "How do I refund a member payment?"}).json()
    assert all(src["url"] != A for src in r2["sources"])


def test_unanswerable_creates_escalation_and_expert_answer_flows_back(settings):
    deps, _ = build(settings, pages_v1())
    run(deps)
    c = TestClient(create_app(deps))
    q = "What is the airspeed velocity of an unladen swallow?"
    r = c.post("/query", json={"question": q}).json()
    assert r["status"] == "insufficient_evidence" and r["needs_human"] and r["escalation_id"]
    again = c.post("/query", json={"question": q}).json()
    assert again["escalation_id"] == r["escalation_id"]  # no duplicate tasks
    res = c.post(f"/escalations/{r['escalation_id']}/resolve", json={"answer": "About 11 m/s for a European swallow.", "resolved_by": "ann", "reviewed": False}).json()
    assert res["searchable"] is False
    assert c.post("/query", json={"question": q}).json()["status"] == "insufficient_evidence"
    c.post(f"/escalations/{r['escalation_id']}/resolve", json={"answer": "About 11 m/s for a European swallow.", "resolved_by": "ann", "reviewed": True})
    ok = c.post("/query", json={"question": q, "escalate_if_unanswered": False}).json()
    assert ok["status"] == "answered" and ok["sources"][0]["kind"] == "expert"


def test_api_key_and_validation(settings):
    from dataclasses import replace
    deps, _ = build(replace(settings, api_key="k"), pages_v1())
    c = TestClient(create_app(deps))
    assert c.post("/query", json={"question": "hello there"}).status_code == 401
    assert c.post("/query", json={"question": "x"}, headers={"X-API-Key": "k"}).status_code == 422
    assert c.get("/health").status_code == 200 and "x-request-id" in c.get("/health").headers
