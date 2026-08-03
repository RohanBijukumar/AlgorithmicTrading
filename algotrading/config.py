from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "trading_simulator.sqlite3"
DAILY_INTERVAL = "1d"
DEFAULT_PROVIDER = "yahoo"
