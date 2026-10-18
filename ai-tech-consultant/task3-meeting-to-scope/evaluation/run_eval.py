"""Evaluate extraction + change detection with real model calls. Writes a CSV, one row per case. No invented numbers.
  python -m evaluation.run_eval --model claude-haiku-5-5 --out results/scope_haiku.csv
Metrics: pass rate; requirement coverage (expected keywords found); vague-ask detection (open question raised);
verified-evidence rate (quotes that exist in the transcript); change-detection accuracy; latency; cost (needs MODEL_PRICES_JSON)."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from dataclasses import replace
from pathlib import Path

from app.agents import analyst, diff
from app.config import Settings
from app.llm import AnthropicLLM, UsageTracker
from app.schemas import Requirement


async def run(model: str, out: str) -> None:
    s = replace(Settings.from_env(), fast_model=model, strong_model=model)
    tracker = UsageTracker(s.model_prices)
    llm = AnthropicLLM(s, tracker)
    rows = []
    for line in Path("evaluation/cases.jsonl").read_text().splitlines():
        c = json.loads(line)
        t0, before, err = time.perf_counter(), len(tracker.calls), ""
        passed, why = False, ""
        try:
            reqs, _ = await analyst.analyze_transcript(llm, c["transcript"], 1, s)
            blob = " ".join(r.description.lower() for r in reqs)
            coverage = all(k in blob for k in c["expect_keywords"])
            verified = all(e.verified for r in reqs for e in r.evidence) if reqs else False
            if c["type"] == "extract":
                vague_ok = (not c["expect_open_question"]) or any(r.open_questions or r.status != "confirmed" for r in reqs)
                inj_ok = not any(w in blob for w in c.get("must_not_contain", []))
                passed = coverage and vague_ok and verified and inj_ok
                why = "" if passed else f"coverage={coverage} vague_ok={vague_ok} verified={verified} injection_ok={inj_ok}"
            else:
                base = [Requirement(id=b["id"], description=b["description"], status="confirmed") for b in c["baseline"]]
                match = await diff.match_requirements(llm, base, reqs, s)
                rep = diff.compute_change_report(1, base, reqs, match, [])
                kinds = {i.kind for i in rep.items if i.kind != "unchanged" or c["expect_change"] == "unchanged"}
                passed = c["expect_change"] in kinds and verified
                why = "" if passed else f"expected {c['expect_change']}, got {sorted(kinds)}"
        except Exception as e:
            why = f"error: {str(e)[:100]}"
        calls = tracker.calls[before:]
        cost = (sum(x.input_tokens / 1e6 * s.model_prices[x.model][0] + x.output_tokens / 1e6 * s.model_prices[x.model][1] for x in calls if x.model in s.model_prices)
                if s.model_prices else "")
        rows.append({"task": "scope", "case_id": c["id"], "model": model, "type": c["type"], "passed": int(passed),
                     "latency_ms": round((time.perf_counter() - t0) * 1000), "cost_usd": cost if cost == "" else round(cost, 6), "failure_reason": why})
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{model}: {sum(r['passed'] for r in rows)}/{len(rows)} passed -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=Settings.from_env().fast_model)
    ap.add_argument("--out", default="results/scope_eval.csv")
    a = ap.parse_args()
    asyncio.run(run(a.model, a.out))
