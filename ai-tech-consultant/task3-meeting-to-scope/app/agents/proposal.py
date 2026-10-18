"""Agent 5 — Proposal & Review: client-ready draft + a consistency check against scope and estimate. Output is a DRAFT for a human."""
from __future__ import annotations

import json
import re

from app.agents.estimator import Totals
from app.config import Settings
from app.llm import LLM, complete_json
from app.schemas import ProposalDoc, Requirement, ScopeDoc, TaskEstimate

SYSTEM = """You write a client-ready proposal from an approved-for-drafting scope and estimate. Plain English, no hype.
Rules:
- Use ONLY the hour figures and price figures provided in <numbers>. Never invent others. Say prices are indicative until confirmed in writing.
- Do not promise anything marked needs_clarification, unclear or disputed; list it under risks_and_open_questions.
- Do not give calendar dates unless they appear in <constraints>.
Reply ONLY JSON with keys: executive_summary, problem_and_solution, scope_and_deliverables, timeline_and_milestones, effort_and_pricing_assumptions, client_responsibilities, exclusions_and_dependencies, risks_and_open_questions, next_steps (all strings)."""


def check_proposal_consistency(p: ProposalDoc, reqs: list[Requirement], tasks: list[TaskEstimate], totals: Totals) -> list[str]:
    """Deterministic review. Returns issues a human must see before approval."""
    issues: list[str] = []
    text = " ".join(p.model_dump().values())
    allowed = {totals.low, totals.likely, totals.high, totals.price_low, totals.price_likely, totals.price_high}
    for t in tasks:
        allowed.update({t.low_h, t.likely_h, t.high_h})
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(?:hours|hrs|h)\b", text, re.I):
        val = float(m.group(1).replace(",", ""))
        if val not in allowed and not any(abs(val - a) < 0.01 for a in allowed):
            issues.append(f"hours figure '{m.group(0)}' does not match the computed estimate")
    for r in reqs:
        in_scope_text = p.scope_and_deliverables
        if r.id in in_scope_text and (r.removed or r.status != "confirmed" or r.feasibility == "needs_clarification"):
            issues.append(f"{r.id} is {'removed' if r.removed else r.status if r.status != 'confirmed' else 'unverified'} but appears in scope_and_deliverables")
    return issues


async def build_proposal(llm: LLM, scope: ScopeDoc, reqs: list[Requirement], tasks: list[TaskEstimate], totals: Totals,
                         constraints: list[str], settings: Settings) -> tuple[ProposalDoc, list[str]]:
    user = (f"<scope>\n{scope.model_dump_json(indent=1)}\n</scope>\n<numbers>\n{json.dumps(totals.as_dict())}\n"
            f"{json.dumps([t.model_dump() for t in tasks])}\n</numbers>\n<constraints>\n{json.dumps(constraints)}\n</constraints>")
    data, _ = await complete_json(llm, system=SYSTEM, user=user, model=settings.strong_model, max_tokens=3500)
    p = ProposalDoc.model_validate(data)
    return p, check_proposal_consistency(p, reqs, tasks, totals)
