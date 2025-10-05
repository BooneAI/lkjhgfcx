"""SQLite state persistence for loan monitor."""
from __future__ import annotations

import sqlite3
from pathlib import Path

_DB_PATH = Path(__file__).resolve().parent.parent / "loan_monitor.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS loan (
    profile TEXT PRIMARY KEY,
    principal REAL NOT NULL,
    interest REAL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS collateral_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile TEXT NOT NULL,
    btc_amount REAL NOT NULL,
    usdt_amount REAL NOT NULL,
    btc_price REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reserves (
    profile TEXT NOT NULL,
    asset TEXT NOT NULL,
    pledged REAL NOT NULL,
    unpledged REAL NOT NULL,
    PRIMARY KEY (profile, asset)
);

CREATE TABLE IF NOT EXISTS hedging_consent (
    user TEXT PRIMARY KEY,
    consented_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    version TEXT NOT NULL,
    answers TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hedging_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS exchange_positions (
    profile TEXT NOT NULL,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    btc_collateral REAL NOT NULL DEFAULT 0,
    usdt_collateral REAL NOT NULL DEFAULT 0,
    loan_outstanding REAL NOT NULL DEFAULT 0,
    last_sync TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (profile, name)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT,
    action TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ltv_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile TEXT NOT NULL,
    ltv REAL NOT NULL,
    btc_amount REAL NOT NULL,
    usdt_amount REAL NOT NULL,
    btc_price REAL NOT NULL,
    principal REAL NOT NULL,
    interest REAL NOT NULL,
    alert_level TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ltv_history_profile_created
    ON ltv_history(profile, created_at DESC);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Migrate legacy single-profile tables to multi-profile layouts."""

    cur = conn.cursor()

    if _table_exists(conn, "loan"):
        columns = _table_columns(conn, "loan")
        if "profile" not in columns:
            cur.execute("ALTER TABLE loan RENAME TO loan_old")
            cur.execute(
                """
                CREATE TABLE loan (
                    profile TEXT PRIMARY KEY,
                    principal REAL NOT NULL,
                    interest REAL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                "SELECT principal, interest, updated_at FROM loan_old WHERE id=1"
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO loan(profile, principal, interest, updated_at) VALUES(?, ?, ?, ?)",
                    ("default", row[0], row[1], row[2]),
                )
            cur.execute("DROP TABLE loan_old")

    if _table_exists(conn, "collateral_snapshot"):
        columns = _table_columns(conn, "collateral_snapshot")
        if "profile" not in columns:
            cur.execute(
                "ALTER TABLE collateral_snapshot ADD COLUMN profile TEXT NOT NULL DEFAULT 'default'"
            )

    if _table_exists(conn, "reserves"):
        columns = _table_columns(conn, "reserves")
        if "profile" not in columns:
            cur.execute("ALTER TABLE reserves RENAME TO reserves_old")
            cur.execute(
                """
                CREATE TABLE reserves (
                    profile TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    pledged REAL NOT NULL,
                    unpledged REAL NOT NULL,
                    PRIMARY KEY (profile, asset)
                )
                """
            )
            cur.execute("SELECT asset, pledged, unpledged FROM reserves_old")
            for asset, pledged, unpledged in cur.fetchall():
                cur.execute(
                    "INSERT INTO reserves(profile, asset, pledged, unpledged) VALUES(?, ?, ?, ?)",
                    ("default", asset, pledged, unpledged),
                )
            cur.execute("DROP TABLE reserves_old")

    if _table_exists(conn, "exchange_positions"):
        columns = _table_columns(conn, "exchange_positions")
        if "profile" not in columns:
            cur.execute("ALTER TABLE exchange_positions RENAME TO exchange_positions_old")
            cur.execute(
                """
                CREATE TABLE exchange_positions (
                    profile TEXT NOT NULL,
                    name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    btc_collateral REAL NOT NULL DEFAULT 0,
                    usdt_collateral REAL NOT NULL DEFAULT 0,
                    loan_outstanding REAL NOT NULL DEFAULT 0,
                    last_sync TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (profile, name)
                )
                """
            )
            cur.execute(
                "SELECT name, platform, btc_collateral, usdt_collateral, loan_outstanding, last_sync FROM exchange_positions_old"
            )
            for name, platform, btc, usdt, loan, last_sync in cur.fetchall():
                cur.execute(
                    "INSERT INTO exchange_positions(profile, name, platform, btc_collateral, usdt_collateral, loan_outstanding, last_sync)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?)",
                    ("default", name, platform, btc, usdt, loan, last_sync),
                )
            cur.execute("DROP TABLE exchange_positions_old")

    conn.commit()


def get_connection(
    path: Path | None = None,
    *,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Return a SQLite connection and ensure schema exists."""
    db_path = path or _DB_PATH
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    _migrate_schema(conn)
    conn.executescript(_SCHEMA)
    return conn


__all__ = ["get_connection"]
