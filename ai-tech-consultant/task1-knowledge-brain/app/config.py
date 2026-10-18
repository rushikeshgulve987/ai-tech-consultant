"""All settings come from environment variables so nothing secret lives in code."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str
    sitemap_url: str
    user_agent: str
    max_concurrency: int
    request_delay_s: float
    max_attempts: int
    max_images_per_article: int
    max_image_bytes: int
    answer_model: str
    vision_model: str
    embedder: str  # "fastembed" | "hash"
    min_evidence_score: float
    top_k: int
    api_key: str | None
    daily_ingest_cron_hour: int
    enable_scheduler: bool
    model_prices: dict[str, tuple[float, float]]  # USD per million tokens (in, out)
    escalation_eval_file: str

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.getenv
        return cls(
            db_path=e("KB_DB_PATH", "data/kb.sqlite3"),
            sitemap_url=e("KB_SITEMAP_URL", "https://support.brilliantdirectories.com/support/sitemap.xml"),
            user_agent=e("KB_USER_AGENT", "AICapstoneBot/0.1 (student project)"),
            max_concurrency=int(e("KB_MAX_CONCURRENCY", "3")),
            request_delay_s=float(e("KB_REQUEST_DELAY_S", "1.0")),
            max_attempts=int(e("KB_MAX_ATTEMPTS", "4")),
            max_images_per_article=int(e("KB_MAX_IMAGES_PER_ARTICLE", "6")),
            max_image_bytes=int(e("KB_MAX_IMAGE_BYTES", str(4_000_000))),
            answer_model=e("KB_ANSWER_MODEL", "claude-sonnet-5-5"),
            vision_model=e("KB_VISION_MODEL", "claude-haiku-5-5"),
            embedder=e("KB_EMBEDDER", "fastembed"),
            min_evidence_score=float(e("KB_MIN_EVIDENCE_SCORE", "0.45")),
            top_k=int(e("KB_TOP_K", "6")),
            api_key=e("KB_API_KEY") or None,
            daily_ingest_cron_hour=int(e("KB_DAILY_INGEST_HOUR", "3")),
            enable_scheduler=e("KB_ENABLE_SCHEDULER", "1") == "1",
            # Fill from the official pricing page, e.g. {"claude-sonnet-5-5":[3,15]}. Empty = cost not reported.
            model_prices={k: tuple(v) for k, v in json.loads(e("MODEL_PRICES_JSON", "{}")).items()},
            escalation_eval_file=e("KB_ESCALATION_EVAL_FILE", "evaluation/cases_from_escalations.jsonl"),
        )
