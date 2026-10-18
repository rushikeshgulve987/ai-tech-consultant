"""Orchestrates the five agents. Initial meeting -> full draft; follow-up -> diff against approved baseline, re-estimate only what changed."""
from __future__ import annotations

from dataclasses import dataclass

from app.agents import analyst, diff, estimator, feasibility, proposal, scope
from app.config import Settings
from app.knowledge_client import KnowledgeClient
from app.llm import LLM
from app.schemas import ChangeReport, Requirement, TaskEstimate
from app.store import Store


@dataclass
class Deps:
    settings: Settings
    llm: LLM
    kb: KnowledgeClient
    store: Store


def assign_ids(reqs: list[Requirement], start: int = 0) -> list[Requirement]:
    for i, r in enumerate(reqs, start + 1):
        r.id = f"REQ-{i:03d}"
    return reqs


async def process_meeting(d: Deps, project_id: int, meeting_id: int) -> int:
    s, store = d.settings, d.store
    m = store.meeting(meeting_id)
    new_reqs, constraints = await analyst.analyze_transcript(d.llm, m["transcript"], meeting_id, s)
    store.set_constraints(meeting_id, constraints)
    base = store.baseline(project_id)
    report: ChangeReport | None = None
    carried_tasks: list[TaskEstimate] = []

    if base is None:
        reqs = assign_ids(new_reqs)
        reqs = await feasibility.check_all(d.llm, d.kb, reqs, s) + [r for r in reqs if r.removed]
        to_estimate = reqs
        all_constraints = constraints
        meeting_ids = [meeting_id]
    else:
        baseline_reqs = [Requirement.model_validate(r) for r in base["requirements"]]
        match = await diff.match_requirements(d.llm, baseline_reqs, new_reqs, s)
        report = diff.compute_change_report(base["version_no"], baseline_reqs, new_reqs, match, constraints)
        reqs = diff.apply_changes(baseline_reqs, new_reqs, match, report)
        changed_ids = {c.requirement_id for c in report.items if c.kind == "modified"} | {r.id for r in reqs if r.id not in {b.id for b in baseline_reqs}}
        to_check = [r for r in reqs if r.id in changed_ids and not r.removed]
        checked = {r.id: r for r in await feasibility.check_all(d.llm, d.kb, to_check, s)}
        reqs = [checked.get(r.id, r) for r in reqs]
        to_estimate = [r for r in reqs if r.id in changed_ids]
        keep = {r.id for r in reqs if not r.removed and r.id not in changed_ids}
        carried_tasks = [TaskEstimate.model_validate(t) for t in base["estimate"]["tasks"] if t["requirement_id"] in keep]
        all_constraints = list(dict.fromkeys(constraints))
        meeting_ids = sorted(set(base["meeting_ids"] + [meeting_id]))

    new_tasks = await estimator.estimate(d.llm, to_estimate, s)
    tasks = carried_tasks + new_tasks
    totals = estimator.compute_totals(tasks, s)
    scope_doc = await scope.build_scope(d.llm, reqs, s, all_constraints)
    prop, issues = await proposal.build_proposal(d.llm, scope_doc, reqs, tasks, totals, all_constraints, s)
    if any(r.feasibility == "needs_clarification" for r in reqs if not r.removed):
        issues.append("Some requirements have unverified product capability: confirm with an expert before sending.")
    return store.create_version(project_id, requirements=[r.model_dump() for r in reqs], scope=scope_doc.model_dump(),
                                estimate={"tasks": [t.model_dump() for t in tasks], "totals": totals.as_dict()}, proposal=prop.model_dump(),
                                change_report=report.model_dump() if report else None, issues=issues, meeting_ids=meeting_ids)
