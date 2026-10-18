"""Typed records passed between agents. Every stage has a schema; model output that fails validation is retried or rejected."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ReqStatus = Literal["confirmed", "unclear", "disputed"]
Feasibility = Literal["existing_configuration", "existing_with_limitation", "custom_development", "needs_clarification"]


class Evidence(BaseModel):
    meeting_id: int
    lines: str                 # e.g. "L12-L14"
    quote: str                 # verbatim text from the transcript
    verified: bool = False     # set by code, never by the model


class Requirement(BaseModel):
    id: str = ""               # stable id, e.g. REQ-001, assigned in code
    description: str
    origin: Literal["explicit", "inferred"] = "explicit"   # said by client vs deduced by the agent
    status: ReqStatus = "unclear"
    priority: Literal["must", "should", "optional"] = "should"
    acceptance_criteria: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    feasibility: Feasibility | None = None
    feasibility_note: str = ""
    kb_sources: list[str] = Field(default_factory=list)
    meeting_ids: list[int] = Field(default_factory=list)
    removed: bool = False       # client dropped it in a later meeting


class RawRequirement(BaseModel):
    """What the extraction model returns per chunk (before ids/evidence verification)."""
    description: str
    origin: Literal["explicit", "inferred"] = "explicit"
    status: ReqStatus = "unclear"
    priority: Literal["must", "should", "optional"] = "should"
    quote: str = ""
    lines: str = ""
    open_questions: list[str] = Field(default_factory=list)
    removed: bool = False      # client explicitly dropped this in the meeting
    deadline_or_budget_note: str = ""


class ChunkExtraction(BaseModel):
    requirements: list[RawRequirement] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class FeasibilityVerdict(BaseModel):
    feasibility: Feasibility
    note: str


class ScopeDoc(BaseModel):
    objective: str
    current_problem: str
    agreed_requirements: list[str]
    proposed_solution: str
    deliverables: list[str]
    exclusions: list[str]
    assumptions: list[str]
    acceptance_criteria: list[str]
    client_responsibilities: list[str]
    open_questions: list[str]
    risks: list[str]


class TaskEstimate(BaseModel):
    requirement_id: str
    task: str
    low_h: float = Field(ge=0)
    likely_h: float = Field(ge=0)
    high_h: float = Field(ge=0)
    assumptions: str = ""
    confidence: Literal["low", "medium", "high"] = "medium"


class EstimateSet(BaseModel):
    tasks: list[TaskEstimate]


class ProposalDoc(BaseModel):
    executive_summary: str
    problem_and_solution: str
    scope_and_deliverables: str
    timeline_and_milestones: str
    effort_and_pricing_assumptions: str
    client_responsibilities: str
    exclusions_and_dependencies: str
    risks_and_open_questions: str
    next_steps: str


class MatchEntry(BaseModel):
    new_index: int
    baseline_id: str | None = None   # None = no counterpart in the baseline
    change: Literal["unchanged", "modified", "new"] = "new"
    what_changed: str = ""


class MatchResult(BaseModel):
    matches: list[MatchEntry]


class ChangeItem(BaseModel):
    kind: Literal["added", "removed", "modified", "unchanged"]
    requirement_id: str
    description: str
    detail: str = ""
    out_of_scope: bool = False
    needs_requote: bool = False


class ChangeReport(BaseModel):
    baseline_version: int
    items: list[ChangeItem]
    date_or_budget_changes: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        out = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
        for i in self.items:
            out[i.kind] += 1
        return out
