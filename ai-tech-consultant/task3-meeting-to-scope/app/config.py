from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str
    kb_url: str
    kb_api_key: str | None
    kb_timeout_s: float
    fast_model: str       # extraction / classification
    strong_model: str     # scope, estimates, proposal
    hourly_rate: float
    currency: str
    tax_rate: float
    chunk_lines: int
    chunk_overlap: int
    model_prices: dict[str, tuple[float, float]]

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.getenv
        return cls(
            db_path=e("SCOPE_DB_PATH", "data/scope.sqlite3"),
            kb_url=e("KB_URL", "http://localhost:8001"),
            kb_api_key=e("KB_API_KEY") or None,
            kb_timeout_s=float(e("KB_TIMEOUT_S", "30")),
            fast_model=e("SCOPE_FAST_MODEL", "claude-haiku-5-5"),
            strong_model=e("SCOPE_STRONG_MODEL", "claude-sonnet-5-5"),
            hourly_rate=float(e("HOURLY_RATE", "50")),   # configurable; NOT a quote until a human approves
            currency=e("CURRENCY", "USD"),
            tax_rate=float(e("TAX_RATE", "0")),
            chunk_lines=int(e("TRANSCRIPT_CHUNK_LINES", "40")),
            chunk_overlap=int(e("TRANSCRIPT_CHUNK_OVERLAP", "6")),
            model_prices={k: tuple(v) for k, v in json.loads(e("MODEL_PRICES_JSON", "{}")).items()},
        )
