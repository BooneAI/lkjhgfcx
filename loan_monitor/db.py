"""SQLite state persistence for loan monitor."""
from __future__ import annotations

import sqlite3
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent / "loan_monitor.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS loan (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    principal REAL NOT NULL,
    interest REAL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS collateral_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    btc_amount REAL NOT NULL,
    usdt_amount REAL NOT NULL,
    btc_price REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reserves (
    asset TEXT PRIMARY KEY,
    pledged REAL NOT NULL,
    unpledged REAL NOT NULL
);
"""


def get_connection(path: Path | None = None) -> sqlite3.Connection:
    """Return a SQLite connection and ensure schema exists."""
    db_path = path or _DB_PATH
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


__all__ = ["get_connection"]
