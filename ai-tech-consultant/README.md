# AI Tech Consultant

One system for a software/service business: it **knows the product** (Task 1), **drafts customer replies** (Task 2) and **turns client meetings into scope, estimate and proposal** (Task 3). Tasks 2 and 3 call Task 1 only through its HTTP API.

| Task | Folder | Language | Port |
|---|---|---|---|
| 1 Knowledge Brain (crawl, images, RAG, escalation) | `task1-knowledge-brain` | Python / FastAPI | 8001 |
| 2 Inbox Agent (classify, draft, human review, learning) | `task2-inbox-agent` | TypeScript (strict) / Express | 3002 |
| 3 Meeting to Scope (5 agents, change tracking, approval) | `task3-meeting-to-scope` | Python / FastAPI | 8003 |

## Prerequisites
Python 3.11+, Node 20+, an Anthropic API key. (First run downloads a small local embedding model.)

## Run
```bash
cp .env.example .env        # add ANTHROPIC_API_KEY and a KB_API_KEY of your choice
./run.sh                    # installs deps, starts all three services (logs in ./logs)
./run.sh ingest 15          # in a second terminal: crawl the first 15 help-center pages, check answers, then:
./run.sh ingest 2000        # full crawl (polite: 3 parallel, 1 s delay, resumes after a stop)
./run.sh test               # all test suites (no API key needed)
```
Open `http://localhost:8001` (ask questions), `:3002` (inbox review), `:8003` (scope builder). API docs: `:8001/docs`.

## Configuration
Everything is an environment variable in `.env` (models, rates, ports). Secrets are never committed. Without a key the Inbox Agent uses an offline mock model (demo only; evals refuse to run on it).

## Evaluations
```bash
cd task1-knowledge-brain && python -m evaluation.build_cases --n 25 && python -m evaluation.run_eval --model claude-sonnet-5-5
cd task2-inbox-agent && npm run eval -- --split dev --draft-model claude-sonnet-5-5
cd task3-meeting-to-scope && python -m evaluation.run_eval --model claude-haiku-5-5
```
Results land in each task's `results/*.csv`; `EVALS.md` reports them. See `DESIGN.md` for architecture and limits.
