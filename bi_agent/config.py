"""Configuration for bi_agent, read from environment variables (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "bi_agent.db"
DEFAULT_RAW_DATA_DIR = PROJECT_ROOT / "raw_data"
DEFAULT_CHARTS_DIR = PROJECT_ROOT / "charts_out"


@dataclass(frozen=True)
class Settings:
    db_path: Path
    raw_data_dir: Path
    charts_dir: Path
    max_query_rows: int
    sentiment_backend: str  # "auto" | "transformer" | "lexicon"


def get_settings() -> Settings:
    """
    BI_AGENT_DB_PATH          -- where the SQLite database lives
    BI_AGENT_RAW_DATA_DIR     -- folder with the Olist CSVs (see raw_data/SOURCES.md)
    BI_AGENT_CHARTS_DIR       -- where chart PNGs are written
    BI_AGENT_MAX_QUERY_ROWS   -- hard cap on rows any tool query can return
    BI_AGENT_SENTIMENT_BACKEND -- "auto" tries a transformer model and falls
        back to a small lexicon scorer if it can't be loaded (no internet,
        package missing, etc). Force one explicitly with "transformer" or
        "lexicon".
    """
    return Settings(
        db_path=Path(os.getenv("BI_AGENT_DB_PATH", str(DEFAULT_DB_PATH))),
        raw_data_dir=Path(os.getenv("BI_AGENT_RAW_DATA_DIR", str(DEFAULT_RAW_DATA_DIR))),
        charts_dir=Path(os.getenv("BI_AGENT_CHARTS_DIR", str(DEFAULT_CHARTS_DIR))),
        max_query_rows=int(os.getenv("BI_AGENT_MAX_QUERY_ROWS", "500")),
        sentiment_backend=os.getenv("BI_AGENT_SENTIMENT_BACKEND", "auto"),
    )
