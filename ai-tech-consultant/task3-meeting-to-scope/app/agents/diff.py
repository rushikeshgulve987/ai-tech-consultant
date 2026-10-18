"""Follow-up handling. Compares a new meeting against the APPROVED BASELINE (never against an unapproved AI draft)."""
from __future__ import annotations

import json
import re

from app.config import Settings
from app.llm import LLM, complete_json
from app.schemas import ChangeItem, ChangeReport, MatchEntry, MatchResult, Requirement

SYSTEM = """You match requirements extracted from a FOLLOW-UP meeting against the approved baseline requirements.
For each new candidate (by index) say which baseline id it refers to, or null if it is a genuinely new need.
change: "unchanged" (same need, nothing new), "modified" (same need but scope/behaviour/priority changed; say what in what_changed), "new".
A candidate marked removed=true that matches a baseline id means the client dropped it. Match by meaning, not wording.
Reply ONLY JSON: {"matches":[{"new_index":int,"baseline_id":str|null,"change":"unchanged|modified|new","what_changed":str}]}"""

DATE_BUDGET = re.compile(r"\b(deadline|deliver\w*|launch|go-live|budget|dollars?|rupees?|weeks?|months?|dates?|moved|postponed|extended|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*|\d{1,2}(?:st|nd|rd|th))\b|[₹$€£]", re.I)


async def match_requirements(llm: LLM, baseline: list[Requirement], new: list[Requirement], settings: Settings) -> MatchResult:
    b = "\n".join(f"{r.id}: {r.description}" for r in baseline if not r.removed)
    n = "\n".join(f"{i}: {r.description} (removed={r.removed})" for i, r in enumerate(new))
    data, _ = await complete_json(llm, system=SYSTEM, user=f"<baseline>\n{b}\n</baseline>\n<new_candidates>\n{n}\n</new_candidates>",
                                  model=settings.strong_model, max_tokens=2500)
    return MatchResult.model_validate(data)


def compute_change_report(baseline_version: int, baseline: list[Requirement], new: list[Requirement], match: MatchResult,
                          new_constraints: list[str]) -> ChangeReport:
    """Pure function, so the labels (added / removed / modified / out-of-scope) are testable without a model."""
    base_by_id = {r.id: r for r in baseline}
    by_idx = {m.new_index: m for m in match.matches}
    items: list[ChangeItem] = []
    touched: set[str] = set()
    for i, r in enumerate(new):
        m = by_idx.get(i, MatchEntry(new_index=i))
        bid = m.baseline_id if m.baseline_id in base_by_id else None   # hallucinated ids are ignored
        if bid is None:
            if r.removed:
                continue  # client dropped something that was never in the baseline: nothing to report
            items.append(ChangeItem(kind="added", requirement_id=f"NEW-{i + 1}", description=r.description,
                                    detail="Not in the approved baseline: flag for clarification and a separate quotation.",
                                    out_of_scope=True, needs_requote=True))
            continue
        touched.add(bid)
        base = base_by_id[bid]
        if r.removed:
            items.append(ChangeItem(kind="removed", requirement_id=bid, description=base.description, detail="Client removed this deliverable.", needs_requote=True))
        elif m.change == "modified":
            items.append(ChangeItem(kind="modified", requirement_id=bid, description=r.description, detail=m.what_changed or "Behaviour or scope changed.", needs_requote=True))
        else:
            items.append(ChangeItem(kind="unchanged", requirement_id=bid, description=base.description))
    for r in baseline:   # baseline items not mentioned stay as they were
        if r.id not in touched and not r.removed:
            items.append(ChangeItem(kind="unchanged", requirement_id=r.id, description=r.description, detail="Not discussed in the follow-up."))
    changes = [c for c in new_constraints if DATE_BUDGET.search(c)]
    return ChangeReport(baseline_version=baseline_version, items=items, date_or_budget_changes=changes)


def apply_changes(baseline: list[Requirement], new: list[Requirement], match: MatchResult, report: ChangeReport) -> list[Requirement]:
    """Revised requirement set: baseline ids stay stable; additions get the next free id; removals are flagged, not deleted."""
    merged = {r.id: r.model_copy(deep=True) for r in baseline}
    by_idx = {m.new_index: m for m in match.matches}
    n = max([int(r.id.split("-")[1]) for r in baseline] + [0])
    kinds = {c.requirement_id: c for c in report.items}
    for i, r in enumerate(new):
        m = by_idx.get(i)
        bid = m.baseline_id if m and m.baseline_id in merged else None
        if bid is None:
            if r.removed:
                continue
            n += 1
            nr = r.model_copy(deep=True, update={"id": f"REQ-{n:03d}", "status": "unclear" if r.status == "confirmed" else r.status})
            nr.open_questions.append("New in follow-up: not in approved baseline; needs confirmation and a quote.")
            merged[nr.id] = nr
        elif r.removed:
            merged[bid].removed = True
            merged[bid].meeting_ids += r.meeting_ids
        elif kinds.get(bid) and kinds[bid].kind == "modified":
            old = merged[bid]
            upd = r.model_copy(deep=True, update={"id": bid, "meeting_ids": sorted(set(old.meeting_ids + r.meeting_ids)), "evidence": old.evidence + r.evidence,
                                                  "feasibility": None, "feasibility_note": ""})
            upd.status = "unclear"   # a changed requirement needs re-confirmation
            merged[bid] = upd
    return list(merged.values())
