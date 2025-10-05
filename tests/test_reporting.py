from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from loan_monitor.config import Config, Thresholds, LoanProfileSettings
from loan_monitor.db import get_connection
from loan_monitor.notifications.base import Notifier
from loan_monitor.services.monitor import LTVMonitor
from loan_monitor.services.reporting import ReportingService


class SilentNotifier(Notifier):
    async def send(self, level: str, message: str) -> None:  # pragma: no cover - side effect only
        return None


def build_config(*, principal: float = 100.0, collateral: dict | None = None) -> Config:
    thresholds = Thresholds()
    profile = LoanProfileSettings(
        id="default",
        name="default",
        loan={"principal": principal, "interest": 0.0},
        collateral=collateral or {"btc": 1.0, "usdt": 0.0},
        reserves={},
        policy={"type": "manual"},
        thresholds=thresholds,
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    return Config(
        api_keys={},
        loan=profile.loan,
        collateral=profile.collateral,
        thresholds=thresholds,
        reserves=profile.reserves,
        policy=profile.policy,
        profiles={"default": profile},
        default_profile="default",
    )


def test_monitor_persists_ltv_history(monkeypatch):
    cfg = build_config(principal=100.0)
    conn = get_connection(Path(":memory:"))
    notifier = SilentNotifier()
    monitor = LTVMonitor(cfg, notifier=notifier, conn=conn)

    async def fake_price():
        return 100.0

    monkeypatch.setattr(monitor.price_service, "get_price", fake_price)
    asyncio.run(monitor.check_once())

    cur = conn.cursor()
    cur.execute(
        "SELECT profile, ltv, alert_level FROM ltv_history ORDER BY id"
    )
    row = cur.fetchone()
    assert row[0] == "default"
    assert pytest.approx(row[1], rel=1e-6) == 1.0
    assert row[2] == "liquidation"


def test_reporting_summary_and_export(tmp_path):
    cfg = build_config(principal=100.0)
    db_path = tmp_path / "history.db"
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO ltv_history (
            profile, ltv, btc_amount, usdt_amount, btc_price,
            principal, interest, alert_level, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "default",
                0.50,
                1.0,
                400.0,
                20000.0,
                100.0,
                0.0,
                "none",
                "2024-01-01T00:00:00",
            ),
            (
                "default",
                0.84,
                1.0,
                250.0,
                19500.0,
                100.0,
                0.0,
                "margin_call",
                "2024-01-01T01:00:00",
            ),
            (
                "default",
                0.93,
                0.9,
                150.0,
                18000.0,
                100.0,
                0.0,
                "liquidation",
                "2024-01-01T02:00:00",
            ),
        ],
    )
    conn.commit()

    reporting = ReportingService(cfg, conn=conn)
    summary = reporting.summarize_ltv()
    assert summary["count"] == 3
    assert summary["levels"]["margin_call"] == 1
    assert summary["levels"]["liquidation"] == 1
    assert summary["margin_events"] == 2
    assert summary["latest"]["ltv"] == pytest.approx(0.93)
    assert summary["change"] == pytest.approx(0.43)
    history = reporting.get_ltv_history()
    assert [round(entry.ltv, 2) for entry in history] == [0.50, 0.84, 0.93]

    csv_path = tmp_path / "ltv.csv"
    reporting.export_history(csv_path, limit=None)
    assert csv_path.exists()
    contents = csv_path.read_text().strip().splitlines()
    assert contents[0].startswith("created_at,ltv")
    assert len(contents) == 4  # header + 3 entries
