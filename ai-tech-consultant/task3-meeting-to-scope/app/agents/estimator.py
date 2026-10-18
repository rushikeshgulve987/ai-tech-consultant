"""Agent 4 — Estimation: small engineering tasks with low/likely/high hours. Totals are computed in CODE, never by the model."""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.config import Settings
from app.llm import LLM, complete_json
from app.schemas import EstimateSet, Requirement, TaskEstimate

SYSTEM = """You estimate developer hours. For each requirement break the work into small tasks (investigation, backend, frontend, integration, tests, acceptance testing, deployment/docs as relevant).
Rules:
- existing_configuration -> only a short configuration + verification task (usually 1-3 h), no custom code.
- needs_clarification or status unclear -> include an explicit 'clarification/investigation' task and set confidence "low".
- Provide low_h <= likely_h <= high_h for every task and a one-line `assumptions` text. Do NOT give totals, prices or delivery dates.
Reply ONLY JSON: {"tasks":[{"requirement_id","task","low_h","likely_h","high_h","assumptions","confidence"}]}"""


@dataclass
class Totals:
    low: float
    likely: float
    high: float
    price_likely: float
    price_low: float
    price_high: float
    currency: str

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def clean_tasks(tasks: list[TaskEstimate], valid_ids: set[str]) -> list[TaskEstimate]:
    """Drop tasks for unknown requirements and repair impossible orderings (low<=likely<=high)."""
    out = []
    for t in tasks:
        if t.requirement_id not in valid_ids:
            continue
        lo, li, hi = sorted([t.low_h, t.likely_h, t.high_h])
        out.append(t.model_copy(update={"low_h": lo, "likely_h": li, "high_h": hi}))
    return out


def compute_totals(tasks: list[TaskEstimate], settings: Settings) -> Totals:
    lo, li, hi = (round(sum(getattr(t, k) for t in tasks), 2) for k in ("low_h", "likely_h", "high_h"))
    mult = settings.hourly_rate * (1 + settings.tax_rate)
    return Totals(lo, li, hi, round(li * mult, 2), round(lo * mult, 2), round(hi * mult, 2), settings.currency)


async def estimate(llm: LLM, reqs: list[Requirement], settings: Settings) -> list[TaskEstimate]:
    active = [r for r in reqs if not r.removed]
    if not active:
        return []
    payload = [r.model_dump(include={"id", "description", "status", "priority", "feasibility", "feasibility_note", "open_questions"}) for r in active]
    data, _ = await complete_json(llm, system=SYSTEM, user=f"<requirements>\n{json.dumps(payload, indent=1)}\n</requirements>",
                                  model=settings.strong_model, max_tokens=3500)
    return clean_tasks(EstimateSet.model_validate(data).tasks, {r.id for r in active})
