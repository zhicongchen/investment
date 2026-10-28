from __future__ import annotations

import os
from pathlib import Path

from shared.env import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = BASE_DIR / "backend"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(BACKEND_DIR / ".env", BASE_DIR / ".env")

DATABASE_URL = f"sqlite:///{DATA_DIR / 'dashboard.db'}"
DEFAULT_REFRESH_CRON_HOUR_ET = 17
DEFAULT_REFRESH_CRON_MINUTE_ET = 10
FMP_API_KEY = os.getenv("FMP_API_KEY", "").strip()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5").strip()
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))
OPTIONS_REPORT_DIR = Path(os.getenv("OPTIONS_REPORT_DIR", str(BASE_DIR / "reports" / "options")))
MARKET_DATA_PROVIDER = os.getenv("MARKET_DATA_PROVIDER", "moomoo").strip().lower()
MOOMOO_OPEND_HOST = os.getenv("MOOMOO_OPEND_HOST", "127.0.0.1").strip()
MOOMOO_OPEND_PORT = int(os.getenv("MOOMOO_OPEND_PORT", "11111"))
