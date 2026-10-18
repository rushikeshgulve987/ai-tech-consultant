"""Build answerable eval cases with KNOWN ground truth: sample ingested articles, have Claude write a question that only
that article answers. Ground truth = the source url, so no hand-guessing about help-center contents.
Usage: python -m evaluation.build_cases --n 25 --out evaluation/kb_cases_generated.jsonl
Review the output by hand before freezing it (delete ambiguous questions). Image cases: add `type: image` lines manually
for questions whose answer is only visible in a screenshot."""
from __future__ import annotations

import argparse
import asyncio
import json
import random

from app.config import Settings
from app.db import Database
from app.llm import AnthropicLLM, complete_json

SYSTEM = ("Write ONE realistic customer question that can be answered ONLY by the help article below. Do not mention the article. "
          "Return JSON: {\"question\": str, \"must_contain\": [1-3 short keywords the correct answer must mention]}")


async def main(n: int, out: str, seed: int) -> None:
    s = Settings.from_env()
    db = Database(s.db_path)
    llm = AnthropicLLM(s)
    rows = db.query("SELECT url, GROUP_CONCAT(text, '\n') body FROM chunks WHERE kind='text' AND active=1 GROUP BY url")
    random.Random(seed).shuffle(rows)
    with open(out, "w") as f:
        for i, r in enumerate(rows[:n], 1):
            data, _ = await complete_json(llm, system=SYSTEM, user=r["body"][:6000], model=s.answer_model, max_tokens=200)
            f.write(json.dumps({"id": f"KB-G{i:02d}", "type": "answerable", "question": data["question"],
                                "expected_url_substr": r["url"], "must_contain": data.get("must_contain", [])}) + "\n")
    print(f"wrote {min(n, len(rows))} cases to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--out", default="evaluation/kb_cases_generated.jsonl")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    asyncio.run(main(a.n, a.out, a.seed))
