import json
import re
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.config import Settings
from app.knowledge_client import KbResult
from app.llm import LLMResult

ROOT = Path(__file__).resolve().parent.parent
A = (ROOT / "transcripts/meeting_A_website_improvements.txt").read_text()
C = (ROOT / "transcripts/meeting_C_followup_to_A.txt").read_text()


def R(desc, quote, lines, status="confirmed", removed=False, qs=()):
    return {"description": desc, "origin": "explicit", "status": status, "priority": "must", "quote": quote, "lines": lines,
            "open_questions": list(qs), "removed": removed}


class FakeLLM:
    """Scripted stand-in: routes on the system prompt, returns canned JSON for transcripts A and C."""

    def __init__(self):
        self.calls = []

    async def complete(self, *, system, user, model, max_tokens=1024, images=None):
        self.calls.append(system[:40])
        if "reading part of a client meeting" in system:
            if "second call" in user:
                out = {"requirements": [
                    R("Rating filter on search", "the rating filter, we now definitely want it", "L2"),
                    R("Featured vendor placement at top of results", "Vendors should be able to mark themselves as \"featured\"", "L5"),
                    R("Search box visible on mobile homepage", "search box visible on the mobile homepage", "L8"),
                    R("AI search", "Forget the AI search completely", "L12", removed=True)],
                    "decisions": [], "constraints": ["Expo moved to 5th of December", "Budget up to 1,500 dollars"]}
            else:
                out = {"requirements": [
                    R("Search filters for location and price", "Location and price are must-haves.", "L8"),
                    R("Rating filter", "Rating, I'm not sure, maybe later", "L8", status="unclear", qs=["Do you want a rating filter now?"]),
                    R("Search box visible on mobile homepage", "the search box should be visible on the homepage", "L12"),
                    R("AI-powered smart search", "Something like ChatGPT but for our vendors", "L16", status="unclear", qs=["What does AI search mean concretely?"])],
                    "decisions": [], "constraints": ["Expo on 20th of November", "No fixed budget"]}
            return LLMResult(json.dumps(out), model)
        if "merge duplicate" in system:
            n = len(re.findall(r"^\d+:", user, re.M))
            return LLMResult(json.dumps({"groups": [{"indices": [i], "description": "x", "status": "confirmed", "priority": "must", "open_questions": []} for i in range(n)]}), model)
        if "judge whether a client requirement" in system:
            return LLMResult(json.dumps({"feasibility": "existing_configuration" if "filter" in user.lower() else "custom_development", "note": "per docs"}), model)
        if "write a scope of work" in system:
            return LLMResult(json.dumps({"objective": "Better vendor search", "current_problem": "Hard to find vendors",
                "agreed_requirements": ["REQ-001: filters", "REQ-004: AI search"], "proposed_solution": "Configure filters", "deliverables": ["Filters"],
                "exclusions": [], "assumptions": [], "acceptance_criteria": ["Customers can filter by location and price"], "client_responsibilities": ["Provide admin access"],
                "open_questions": [], "risks": []}), model)
        if "estimate developer hours" in system:
            ids = re.findall(r'"id": "(REQ-\d+)"', user)
            return LLMResult(json.dumps({"tasks": [{"requirement_id": i, "task": f"Work for {i}", "low_h": 5, "likely_h": 3, "high_h": 8, "assumptions": "a", "confidence": "medium"} for i in ids] +
                                         [{"requirement_id": "REQ-999", "task": "ghost", "low_h": 1, "likely_h": 1, "high_h": 1}]}), model)
        if "client-ready proposal" in system:
            tot = json.loads(re.search(r"<numbers>\n(\{.*?\})\n", user).group(1))
            body = f"Estimated {tot['likely']} hours."
            return LLMResult(json.dumps({k: body if k == "effort_and_pricing_assumptions" else "text" for k in
                ["executive_summary", "problem_and_solution", "scope_and_deliverables", "timeline_and_milestones", "effort_and_pricing_assumptions",
                 "client_responsibilities", "exclusions_and_dependencies", "risks_and_open_questions", "next_steps"]}), model)
        if "match requirements extracted from a FOLLOW-UP" in system:
            return LLMResult(json.dumps({"matches": [
                {"new_index": 0, "baseline_id": "REQ-002", "change": "modified", "what_changed": "now confirmed as must-have"},
                {"new_index": 1, "baseline_id": None, "change": "new"},
                {"new_index": 2, "baseline_id": "REQ-003", "change": "unchanged"},
                {"new_index": 3, "baseline_id": "REQ-004", "change": "modified"},
                {"new_index": 4, "baseline_id": "REQ-777", "change": "modified"}]}), model)
        raise AssertionError("unexpected prompt: " + system[:60])


class FakeKB:
    def __init__(self, kind="answered"):
        self.kind = kind

    async def query(self, question, context=None):
        return KbResult(self.kind, "Filters are configurable in Settings > Search." if self.kind == "answered" else "", ["https://docs/search"] if self.kind == "answered" else [])


@pytest.fixture
def settings():
    return replace(Settings.from_env(), db_path=":memory:", hourly_rate=50.0, tax_rate=0.0, currency="USD")
