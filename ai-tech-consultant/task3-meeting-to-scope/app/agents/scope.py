"""Agent 3 — Scope: turns structured requirements into a scope-of-work document. Never silently promotes uncertain items."""
from __future__ import annotations

import json
import re

from app.config import Settings
from app.llm import LLM, complete_json
from app.schemas import Requirement, ScopeDoc

SYSTEM = """You write a scope of work for a software/service company. Input is structured requirements (JSON) with feasibility results.
Rules:
- `agreed_requirements`: ONLY requirements whose status is "confirmed". Prefix each line with its id, e.g. "REQ-002: ...".
- Requirements marked unclear/disputed go to `open_questions`, never to agreed or deliverables.
- Requirements with feasibility existing_configuration should be delivered as configuration, not custom code. needs_clarification must be listed under risks.
- Removed requirements go to `exclusions`.
- Every deliverable needs a testable acceptance criterion ("customers can filter results by the agreed attributes", never "improve search").
- Do not invent budget, dates or facts. Reply ONLY JSON with keys: objective, current_problem, agreed_requirements, proposed_solution, deliverables, exclusions, assumptions, acceptance_criteria, client_responsibilities, open_questions, risks (lists of strings except objective/current_problem/proposed_solution)."""


def enforce_scope_integrity(scope: ScopeDoc, reqs: list[Requirement]) -> ScopeDoc:
    """Code-level guard: uncertain or removed requirements can never appear as agreed, whatever the model wrote."""
    bad = {r.id: r for r in reqs if r.removed or r.status != "confirmed"}
    keep, moved = [], []
    for line in scope.agreed_requirements:
        ids = set(re.findall(r"REQ-\d+", line))
        if ids & bad.keys():
            moved.append(line)
        else:
            keep.append(line)
    scope.agreed_requirements = keep
    for line in moved:
        rid = next(i for i in re.findall(r"REQ-\d+", line) if i in bad)
        target = scope.exclusions if bad[rid].removed else scope.open_questions
        target.append(f"{line} (not confirmed by the client: moved by integrity check)")
    for r in reqs:
        if r.removed and not any(r.id in e for e in scope.exclusions):
            scope.exclusions.append(f"{r.id}: {r.description} (removed by client)")
        if not r.removed and r.status != "confirmed":
            for q in r.open_questions:
                if not any(q in x for x in scope.open_questions):
                    scope.open_questions.append(f"{r.id}: {q}")
        if r.feasibility == "needs_clarification" and not any(r.id in x for x in scope.risks):
            scope.risks.append(f"{r.id}: product capability not verified. {r.feasibility_note}")
    return scope


async def build_scope(llm: LLM, reqs: list[Requirement], settings: Settings, constraints: list[str]) -> ScopeDoc:
    payload = [r.model_dump(include={"id", "description", "status", "priority", "origin", "feasibility", "feasibility_note", "open_questions", "removed"}) for r in reqs]
    data, _ = await complete_json(llm, system=SYSTEM, model=settings.strong_model, max_tokens=3000,
                                  user=f"<requirements>\n{json.dumps(payload, indent=1)}\n</requirements>\n<constraints_from_meetings>\n{json.dumps(constraints)}\n</constraints_from_meetings>")
    return enforce_scope_integrity(ScopeDoc.model_validate(data), reqs)
