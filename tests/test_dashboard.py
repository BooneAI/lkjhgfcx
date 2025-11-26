import asyncio
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loan_monitor.config import Config, Thresholds
from loan_monitor.db import get_connection
from loan_monitor.services.dashboard import gather_dashboard_snapshot, render_dashboard


class StubPriceService:
    def __init__(self, price: float) -> None:
        self.price = price

    async def get_price(self) -> float:
        return self.price


def test_dashboard_snapshot_renders():
    cfg = Config(
        api_keys={},
        loan={"principal": 100.0, "interest": 0.0},
        collateral={"btc": 0.5, "usdt": 50.0},
        thresholds=Thresholds(),
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={"type": "manual"},
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute("INSERT INTO loan (id, principal, interest) VALUES (1, 100.0, 0.0)")
    cur.execute(
        "INSERT INTO collateral_snapshot (btc_amount, usdt_amount, btc_price) VALUES (?, ?, ?)",
        (0.5, 50.0, 200.0),
    )
    conn.commit()

    snapshot = asyncio.run(
        gather_dashboard_snapshot(cfg, price_service=StubPriceService(200.0), conn=conn)
    )

    expected_ltv = 100.0 / (0.5 * 200.0 + 50.0)
    assert pytest.approx(snapshot.ltv, rel=1e-6) == expected_ltv
    assert snapshot.status == "safe"
    assert snapshot.margin_buffer_drop_pct is not None
    assert snapshot.terms["platform"] == cfg.terms.platform
    assert snapshot.last_loan_update is not None
    assert snapshot.last_collateral_update is not None

    text = render_dashboard(snapshot)
    assert "Status:" in text
    assert "Margin-call buffer" in text
    assert "Platform terms" in text


def test_dashboard_margin_call_recommendation():
    cfg = Config(
        api_keys={},
        loan={"principal": 85.0, "interest": 0.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        thresholds=Thresholds(),
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={"type": "manual"},
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute("INSERT INTO loan (id, principal, interest) VALUES (1, 85.0, 0.0)")
    cur.execute(
        "INSERT INTO collateral_snapshot (btc_amount, usdt_amount, btc_price) VALUES (?, ?, ?)",
        (1.0, 0.0, 100.0),
    )
    conn.commit()

    snapshot = asyncio.run(
        gather_dashboard_snapshot(cfg, price_service=StubPriceService(100.0), conn=conn)
    )

    assert snapshot.status == "margin_call"
    assert math.isclose(snapshot.margin_buffer_pct, 0.0)
    assert snapshot.margin_buffer_drop_pct == 0.0
    assert "margin-call" in snapshot.recommended_action.lower()
