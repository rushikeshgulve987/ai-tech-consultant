from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import Settings
from app.knowledge_client import KnowledgeClient
from app.llm import AnthropicLLM
from app.pipeline import Deps, process_meeting
from app.store import NotApproved, Store


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    client: str = Field(min_length=1, max_length=200)


class MeetingIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    meeting_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    transcript: str = Field(min_length=20, max_length=400_000)


class ApproveIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    acknowledge_issues: bool = False


def create_app(deps: Deps | None = None) -> FastAPI:
    if deps is None:
        s = Settings.from_env()
        deps = Deps(s, AnthropicLLM(s), KnowledgeClient(s.kb_url, s.kb_api_key, s.kb_timeout_s), Store(s.db_path))
    d = deps
    app = FastAPI(title="Meeting to Scope", version="1.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/projects", status_code=201)
    def create_project(p: ProjectIn) -> dict:
        return {"id": d.store.create_project(p.name, p.client)}

    @app.get("/projects")
    def projects() -> list[dict]:
        return d.store.projects()

    @app.post("/projects/{pid}/meetings", status_code=201)
    def add_meeting(pid: int, m: MeetingIn) -> dict:
        try:
            return {"id": d.store.add_meeting(pid, m.title, m.meeting_date, m.transcript)}
        except KeyError:
            raise HTTPException(404, "project not found")

    @app.get("/projects/{pid}/meetings")
    def meetings(pid: int) -> list[dict]:
        return d.store.meetings(pid)

    @app.post("/projects/{pid}/meetings/{mid}/process", status_code=201)
    async def process(pid: int, mid: int) -> dict:
        try:
            d.store.meeting(mid)
        except KeyError:
            raise HTTPException(404, "meeting not found")
        try:
            return {"version_id": await process_meeting(d, pid, mid)}
        except RuntimeError as e:
            raise HTTPException(502, f"model backend error: {e}")
        except ValueError as e:   # schema validation of model output failed after retry
            raise HTTPException(502, f"model returned unusable output: {e}")

    @app.get("/projects/{pid}/versions")
    def versions(pid: int) -> list[dict]:
        return d.store.versions(pid)

    @app.get("/versions/{vid}")
    def version(vid: int) -> dict:
        try:
            return d.store.version(vid)
        except KeyError:
            raise HTTPException(404, "version not found")

    @app.post("/versions/{vid}/approve")
    def approve(vid: int, a: ApproveIn) -> dict:
        try:
            return d.store.approve(vid, a.reviewer, a.acknowledge_issues)
        except KeyError:
            raise HTTPException(404, "version not found")
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/versions/{vid}/send")
    def send(vid: int) -> dict:
        try:
            return d.store.send(vid)
        except KeyError:
            raise HTTPException(404, "version not found")
        except NotApproved as e:
            raise HTTPException(403, str(e))

    static = Path(__file__).resolve().parent.parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static / "index.html")

    return app


def app_factory() -> FastAPI:
    return create_app()
