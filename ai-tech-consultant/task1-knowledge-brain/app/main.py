"""Knowledge Brain HTTP API."""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ingestion.crawler import PoliteFetcher
from ingestion.pipeline import run_ingestion, status as ingestion_status
from retrieval.qa import answer_question, create_escalation, resolve_escalation
from retrieval.store import Store
from .config import Settings
from .db import Database
from .embeddings import make_embedder
from .llm import AnthropicLLM, LLM
from .schemas import EscalationCreate, EscalationResolve, IngestRequest, QueryRequest, QueryResponse

log = logging.getLogger("kb")


class Deps:
    def __init__(self, settings: Settings, llm: LLM | None = None, embedder=None, fetcher: PoliteFetcher | None = None) -> None:
        self.settings = settings
        self.db = Database(settings.db_path)
        self.embedder = embedder or make_embedder(settings.embedder)
        self.store = Store(self.db, self.embedder)
        self.llm = llm or AnthropicLLM(settings)
        self.fetcher = fetcher or PoliteFetcher(settings.user_agent, settings.max_concurrency, settings.request_delay_s, settings.max_attempts)
        self.ingest_lock = asyncio.Lock()


def create_app(deps: Deps | None = None) -> FastAPI:
    d = deps or Deps(Settings.from_env())

    async def scheduled() -> None:
        if not d.ingest_lock.locked():
            async with d.ingest_lock:
                await run_ingestion(db=d.db, store=d.store, fetcher=d.fetcher, llm=d.llm, settings=d.settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sched = None
        if d.settings.enable_scheduler:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            sched = AsyncIOScheduler()
            sched.add_job(scheduled, "cron", hour=d.settings.daily_ingest_cron_hour, id="daily-ingest")
            sched.start()
        yield
        if sched:
            sched.shutdown(wait=False)

    app = FastAPI(title="Knowledge Brain", version="1.0", lifespan=lifespan,
                  description="Shared knowledge service. Auth: send `X-API-Key` if KB_API_KEY is set. Every response carries `X-Request-ID`.")

    def auth(x_api_key: str | None = Header(default=None)) -> None:
        if d.settings.api_key and x_api_key != d.settings.api_key:
            raise HTTPException(401, "invalid or missing X-API-Key")

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        resp = await call_next(request)
        resp.headers["X-Request-ID"] = rid
        return resp

    @app.get("/health")
    def health() -> dict:
        n = d.db.one("SELECT COUNT(*) n FROM chunks WHERE active=1")["n"]
        return {"status": "ok", "active_chunks": n}

    @app.post("/query", response_model=QueryResponse, dependencies=[Depends(auth)])
    async def query(req: QueryRequest) -> QueryResponse:
        try:
            return await asyncio.wait_for(
                answer_question(question=req.question, context=req.context, store=d.store, db=d.db, llm=d.llm, settings=d.settings,
                                escalate=req.escalate_if_unanswered, top_k=req.top_k), timeout=60)
        except asyncio.TimeoutError:
            raise HTTPException(504, "answer generation timed out")
        except RuntimeError as err:
            raise HTTPException(502, f"model backend error: {err}")

    @app.post("/ingestion/run", status_code=202, dependencies=[Depends(auth)])
    async def ingestion_run(req: IngestRequest | None = None) -> dict:
        if d.ingest_lock.locked():
            raise HTTPException(409, "an ingestion run is already in progress")

        async def job() -> None:
            async with d.ingest_lock:
                await run_ingestion(db=d.db, store=d.store, fetcher=d.fetcher, llm=d.llm, settings=d.settings, limit=req.limit if req else None)

        asyncio.create_task(job())
        return {"accepted": True}

    @app.get("/ingestion/status", dependencies=[Depends(auth)])
    def ingestion_state() -> dict:
        return {**ingestion_status(d.db), "running": d.ingest_lock.locked()}

    @app.post("/escalations", status_code=201, dependencies=[Depends(auth)])
    def escalation_create(req: EscalationCreate) -> dict:
        return {"escalation_id": create_escalation(d.db, req.question, req.reason, [])}

    @app.get("/escalations", dependencies=[Depends(auth)])
    def escalation_list(status: str = "open") -> list[dict]:
        return [dict(r) for r in d.db.query("SELECT id,question,reason,status,created_at,answer FROM escalations WHERE status=? ORDER BY id", (status,))]

    @app.post("/escalations/{esc_id}/resolve", dependencies=[Depends(auth)])
    def escalation_resolve(esc_id: int, req: EscalationResolve) -> dict:
        try:
            return resolve_escalation(db=d.db, store=d.store, settings=d.settings, esc_id=esc_id, answer=req.answer, resolved_by=req.resolved_by,
                                      scope_note=req.scope_note, reviewed=req.reviewed, expires_at=req.expires_at)
        except KeyError:
            raise HTTPException(404, "escalation not found")

    static = Path(__file__).resolve().parent.parent / "static"
    if static.exists():
        app.mount("/static", StaticFiles(directory=static), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(static / "index.html")

    return app


def app_factory() -> FastAPI:  # uvicorn app.main:app_factory --factory
    return create_app()
