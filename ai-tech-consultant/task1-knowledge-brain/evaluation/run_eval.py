"""Run Knowledge Brain evals in-process and write a CSV. Nothing is invented: every number comes from a run.
  python -m evaluation.run_eval --model claude-sonnet-5-5 --out results/kb_sonnet.csv
  python -m evaluation.run_eval --retrieval-only          # recall@k ablation: vector vs keyword vs hybrid (no LLM cost)
Metrics: pass rate, retrieval hit (expected source in top-k), citation correctness, abstention correctness,
latency (median/p95), cost per query (needs MODEL_PRICES_JSON)."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import time
from dataclasses import replace
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.embeddings import make_embedder
from app.llm import AnthropicLLM, UsageTracker
from retrieval.qa import answer_question
from retrieval.store import Store

CASE_FILES = ["evaluation/kb_cases_manual.jsonl", "evaluation/kb_cases_generated.jsonl", "evaluation/cases_from_escalations.jsonl"]


def load_cases() -> list[dict]:
    cases = []
    for f in CASE_FILES:
        p = Path(f)
        if p.exists():
            cases += [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return cases


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


async def run(model: str, out: str) -> None:
    s = replace(Settings.from_env(), answer_model=model)
    db, tracker = Database(s.db_path), UsageTracker(s.model_prices)
    store, llm = Store(db, make_embedder(s.embedder)), AnthropicLLM(s, tracker)
    rows = []
    for c in load_cases():
        before = len(tracker.calls)
        t0 = time.perf_counter()
        err = ""
        try:
            r = await answer_question(question=c["question"], context=None, store=store, db=db, llm=llm, settings=s, escalate=False)
        except Exception as e:  # reliability is a metric: count failures, don't hide them
            r, err = None, str(e)[:120]
        lat = time.perf_counter() - t0
        calls = tracker.calls[before:]
        cost = (sum(x.input_tokens / 1e6 * s.model_prices[x.model][0] + x.output_tokens / 1e6 * s.model_prices[x.model][1]
                    for x in calls if x.model in s.model_prices) if s.model_prices else None)
        passed, why = False, err
        if r:
            text = (r.answer or "").lower()
            if c["type"] in ("unanswerable",):
                passed, why = r.status == "insufficient_evidence", "answered an unanswerable question"
            elif c["type"] == "injection":
                passed = not any(b.lower() in text for b in c.get("must_not_contain", []))
                why = "followed injected instruction"
            else:
                cited = any(c["expected_url_substr"] in src.url for src in r.sources)
                kw = all(k.lower() in text for k in c.get("must_contain", []))
                passed = r.status == "answered" and cited and kw
                why = "" if passed else f"status={r.status} cited_expected={cited} keywords_ok={kw}"
        rows.append({"task": "knowledge", "case_id": c["id"], "model": model, "type": c["type"], "passed": int(passed),
                     "latency_ms": round(lat * 1000), "cost_usd": "" if cost is None else round(cost, 6), "failure_reason": "" if passed else why})
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    lat = [r["latency_ms"] for r in rows]
    print(f"{model}: {sum(r['passed'] for r in rows)}/{len(rows)} passed | median {statistics.median(lat)} ms | p95 {pct(lat, .95)} ms | cost total {tracker.cost_usd()}")


def retrieval_only() -> None:
    s = Settings.from_env()
    store = Store(Database(s.db_path), make_embedder(s.embedder))
    cases = [c for c in load_cases() if c["type"] in ("answerable", "from_escalation") and c.get("expected_url_substr")]
    for mode in ("vector", "keyword", "hybrid"):
        hit = sum(any(c["expected_url_substr"] in h.url for h in store.search(c["question"], k=s.top_k, mode=mode)) for c in cases)
        print(f"{mode:8s} recall@{s.top_k}: {hit}/{len(cases)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=Settings.from_env().answer_model)
    ap.add_argument("--out", default="results/kb_eval.csv")
    ap.add_argument("--retrieval-only", action="store_true")
    a = ap.parse_args()
    retrieval_only() if a.retrieval_only else asyncio.run(run(a.model, a.out))
