# EVALS

> **Status: harness complete, results PENDING.** No number below is invented. Run the commands, paste the real values, keep failed cases, then delete this banner. The brief requires at least 20 cases per task and at least 2 models compared on quality, cost and speed.

## Method
Same frozen cases for every model; prompts and settings unchanged between models; failures stay in the CSVs (`results/*.csv`: task, case_id, model, passed, latency_ms, cost_usd, failure_reason). Cost = token counts x the prices you put in `MODEL_PRICES_JSON` (copy from Anthropic's pricing page; empty = not reported). Latency is wall-clock per case; report median and p95. The 38 dev emails tune prompts; the 12 `test` emails are run once, at the end.

## Datasets
| Task | Cases | Where |
|---|---|---|
| 1 Knowledge | 10 hand-written (9 unanswerable, 1 injection) + 25 generated from ingested articles with a known source URL (+ image cases you add by hand, + every escalation) | `task1-knowledge-brain/evaluation/` |
| 2 Inbox | 50 emails (12 how-to, 10 troubleshooting, 6 project, 6 refund/cancel, 6 complaint, 5 quote, 5 follow-up); 38 dev / 12 test | `task2-inbox-agent/data/emails.json` |
| 3 Scope | 24 (20 extraction incl. vague asks and one injection, 4 follow-up change cases) | `task3-meeting-to-scope/evaluation/cases.jsonl` |

Review `kb_cases_generated.jsonl` by hand and delete ambiguous questions before freezing it.

## Results (fill from the CSVs)
| Task | Model | Cases | Passed | Quality % | Median ms | p95 ms | Cost / case | Failures / retries |
|---|---|---|---|---|---|---|---|---|
| Knowledge | claude-sonnet-5-5 | | | | | | | |
| Knowledge | claude-haiku-5-5 | | | | | | | |
| Inbox (classify + draft) | haiku + sonnet | | | | | | | |
| Inbox | sonnet + sonnet | | | | | | | |
| Scope (extract/diff) | claude-haiku-5-5 | | | | | | | |
| Scope | claude-sonnet-5-5 | | | | | | | |

Retrieval ablation (`python -m evaluation.run_eval --retrieval-only`): vector ___/___, keyword ___/___, hybrid ___/___ recall@6.

**Pass definitions.** Knowledge: answered with the expected source cited and keywords present, or abstained on an unanswerable question, or ignored an injected instruction. Inbox: right category, no unsafe commitment, human required where the case says so. Scope: expected keywords found, vague asks raise an open question, every quote verified in the transcript; follow-up cases: the expected change kind is detected.

## Failures and what changed
Write 3-5 real failures (case id, what happened, the fix, before/after pass rate). Show at least one failure in the demo video, for example a question the docs cannot answer, or the Knowledge service stopped while an email arrives.

## Prompt revisions
| Version | Problem it fixes | Before | After | Cost / latency change |
|---|---|---|---|---|
| | | | | |

## Checks already covered by automated tests
Resume after a failed page; removed article disappears from answers; invented citation ids discarded; API key and input validation; no draft when Knowledge is down; duplicate send blocked; 20-streak resets on any edit; autonomy never for refund/complaint; policy guard; unverified quote cannot confirm a requirement; uncertain items kept out of agreed scope; unapproved version cannot be sent or become the baseline. Counts at hand-off: Task 1 = 13, Task 2 = 27, Task 3 = 12 (`./run.sh test`).
