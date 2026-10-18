import pytest

from app.agents.analyst import build_requirements
from app.agents.diff import apply_changes, compute_change_report
from app.agents.estimator import clean_tasks, compute_totals
from app.agents.proposal import check_proposal_consistency
from app.agents.scope import enforce_scope_integrity
from app.chunking import chunk_transcript, verify_quote
from app.pipeline import assign_ids
from app.schemas import MatchEntry, MatchResult, ProposalDoc, RawRequirement, Requirement, ScopeDoc, TaskEstimate
from app.store import NotApproved, Store
from tests.conftest import A


def test_chunks_overlap_and_number_lines():
    ch = chunk_transcript("\n".join(f"line {i}" for i in range(1, 101)), size=40, overlap=6)
    assert ch[0].start == 1 and ch[0].end == 40 and ch[1].start == 35 and ch[-1].end == 100
    assert ch[1].text.startswith("L35: line 35")


def test_quote_verification_is_done_in_code():
    ok, lines = verify_quote(A, "Location and price are must-haves")
    assert ok and lines.startswith("L")
    assert verify_quote(A, "The client wants a blockchain loyalty program")[0] is False
    assert verify_quote(A, "short")[0] is False


def test_requirement_without_verified_evidence_cannot_be_confirmed():
    raws = [RawRequirement(description="Invented", status="confirmed", quote="never said this at all in the meeting", lines="L1"),
            RawRequirement(description="Real", status="confirmed", quote="Location and price are must-haves.", lines="L8")]
    reqs = build_requirements(raws, [[0], [1]], A, meeting_id=1)
    assert reqs[0].status == "unclear" and any("No verifiable" in q for q in reqs[0].open_questions)
    assert reqs[1].status == "confirmed" and reqs[1].evidence[0].verified


def mk(id, desc, status="confirmed", removed=False, **kw):
    return Requirement(id=id, description=desc, status=status, removed=removed, **kw)


def test_scope_integrity_moves_uncertain_and_removed_items():
    reqs = [mk("REQ-001", "filters"), mk("REQ-002", "ai", status="unclear", open_questions=["what is AI search?"]), mk("REQ-003", "old", removed=True),
            mk("REQ-004", "x", feasibility="needs_clarification", feasibility_note="unverified")]
    s = ScopeDoc(objective="o", current_problem="p", agreed_requirements=["REQ-001: filters", "REQ-002: ai", "REQ-003: old"], proposed_solution="s", deliverables=[],
                 exclusions=[], assumptions=[], acceptance_criteria=[], client_responsibilities=[], open_questions=[], risks=[])
    s = enforce_scope_integrity(s, reqs)
    assert s.agreed_requirements == ["REQ-001: filters"]
    assert any("REQ-002" in q for q in s.open_questions) and any("REQ-003" in e for e in s.exclusions)
    assert any("REQ-004" in r for r in s.risks)


def test_estimates_are_repaired_and_totals_computed_in_code(settings):
    tasks = clean_tasks([TaskEstimate(requirement_id="REQ-001", task="t", low_h=5, likely_h=3, high_h=8), TaskEstimate(requirement_id="REQ-9", task="ghost", low_h=1, likely_h=1, high_h=1)], {"REQ-001"})
    assert len(tasks) == 1 and (tasks[0].low_h, tasks[0].likely_h, tasks[0].high_h) == (3, 5, 8)
    t = compute_totals(tasks, settings)
    assert (t.low, t.likely, t.high) == (3, 5, 8) and t.price_likely == 250.0


def test_proposal_consistency_flags_invented_hours(settings):
    tasks = [TaskEstimate(requirement_id="REQ-001", task="t", low_h=3, likely_h=5, high_h=8)]
    totals = compute_totals(tasks, settings)
    base = {k: "text" for k in ProposalDoc.model_fields}
    ok = ProposalDoc(**{**base, "effort_and_pricing_assumptions": "About 5 hours."})
    bad = ProposalDoc(**{**base, "effort_and_pricing_assumptions": "About 40 hours.", "scope_and_deliverables": "REQ-002 AI search"})
    reqs = [mk("REQ-002", "ai", status="unclear")]
    assert check_proposal_consistency(ok, reqs, tasks, totals) == []
    issues = check_proposal_consistency(bad, reqs, tasks, totals)
    assert len(issues) == 2


def test_change_report_labels_and_ignores_hallucinated_ids():
    base = [mk("REQ-001", "filters"), mk("REQ-002", "rating", status="unclear"), mk("REQ-003", "mobile box"), mk("REQ-004", "ai")]
    new = [mk("", "rating now confirmed"), mk("", "featured vendors"), mk("", "ai", removed=True), mk("", "ghost match")]
    match = MatchResult(matches=[MatchEntry(new_index=0, baseline_id="REQ-002", change="modified", what_changed="confirmed"), MatchEntry(new_index=1),
                                 MatchEntry(new_index=2, baseline_id="REQ-004"), MatchEntry(new_index=3, baseline_id="REQ-777", change="modified")])
    rep = compute_change_report(1, base, new, match, ["Deadline moved to December", "Budget 1,500 dollars", "Likes blue"])
    kinds = {(i.requirement_id, i.kind) for i in rep.items}
    assert ("REQ-002", "modified") in kinds and ("REQ-004", "removed") in kinds and ("NEW-2", "added") in kinds
    assert ("REQ-001", "unchanged") in kinds and ("REQ-003", "unchanged") in kinds
    added = [i for i in rep.items if i.kind == "added"]
    assert all(i.out_of_scope and i.needs_requote for i in added) and len(added) == 2   # hallucinated REQ-777 becomes 'added', never silently merged
    assert rep.date_or_budget_changes == ["Deadline moved to December", "Budget 1,500 dollars"]
    merged = {r.id: r for r in apply_changes(base, new, match, rep)}
    assert merged["REQ-004"].removed and merged["REQ-002"].status == "unclear" and merged["REQ-005"].status == "unclear"
    assert set(merged) == {"REQ-001", "REQ-002", "REQ-003", "REQ-004", "REQ-005", "REQ-006"}


def _version(store, pid, issues=()):
    return store.create_version(pid, requirements=[], scope={}, estimate={"tasks": [], "totals": {}}, proposal={}, change_report=None, issues=list(issues), meeting_ids=[1])


def test_nothing_is_sent_without_approval_and_baseline_is_last_approved():
    s = Store(":memory:")
    pid = s.create_project("p", "c")
    v1 = _version(s, pid)
    with pytest.raises(NotApproved):
        s.send(v1)
    assert s.baseline(pid) is None
    s.approve(v1, "Ann")
    assert s.baseline(pid)["id"] == v1
    v2 = _version(s, pid)
    assert s.baseline(pid)["id"] == v1          # an unapproved draft never becomes the baseline
    with pytest.raises(NotApproved):
        s.send(v2)
    s.approve(v2, "Ann")
    assert s.version(v1)["status"] == "superseded" and s.baseline(pid)["id"] == v2
    with pytest.raises(ValueError):
        s.approve(v2, "Ann")                    # already approved


def test_approval_needs_reviewer_and_acknowledged_issues():
    s = Store(":memory:")
    pid = s.create_project("p", "c")
    v = _version(s, pid, issues=["numbers mismatch"])
    with pytest.raises(ValueError):
        s.approve(v, "Ann")
    with pytest.raises(ValueError):
        s.approve(v, "  ", acknowledge_issues=True)
    assert s.approve(v, "Ann", acknowledge_issues=True)["status"] == "approved"
