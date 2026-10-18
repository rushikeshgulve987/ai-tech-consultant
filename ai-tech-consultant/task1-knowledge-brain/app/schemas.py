"""The public API contract (also served as OpenAPI at /docs)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    context: str | None = Field(default=None, max_length=4000, description="Optional extra context from the caller (e.g. email thread).")
    escalate_if_unanswered: bool = True
    top_k: int | None = Field(default=None, ge=1, le=12)


class Source(BaseModel):
    title: str
    url: str
    relevance: float = Field(description="Cosine similarity between question and the cited chunk (0-1). Not a probability.")
    kind: Literal["text", "image", "expert"]
    excerpt: str


class ImageRef(BaseModel):
    image_url: str
    article_url: str
    description: str


class QueryResponse(BaseModel):
    request_id: str
    status: Literal["answered", "insufficient_evidence"]
    answer: str | None
    sources: list[Source]
    images: list[ImageRef]
    evidence_score: float = Field(description="Best cosine similarity among retrieved chunks. Used for the abstain threshold; NOT a calibrated confidence.")
    needs_human: bool
    escalation_id: int | None = None
    note: str | None = None


class EscalationCreate(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    reason: str = "manual"


class EscalationResolve(BaseModel):
    answer: str = Field(min_length=3, max_length=4000)
    resolved_by: str = Field(min_length=1, max_length=100)
    scope_note: str | None = Field(default=None, description="e.g. 'only valid for enterprise plan'")
    reviewed: bool = Field(default=False, description="Only reviewed answers become searchable.")
    expires_at: str | None = Field(default=None, description="ISO timestamp; after this the answer is ignored.")


class IngestRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, description="Process at most N pages (use 10-20 for the first trial).")
