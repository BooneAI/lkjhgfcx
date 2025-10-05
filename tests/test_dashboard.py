import asyncio
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loan_monitor.config import Config, Thresholds, LoanProfileSettings
from loan_monitor.db import get_connection
from loan_monitor.services.dashboard import gather_dashboard_snapshot, render_dashboard


class StubPriceService:
    def __init__(self, price: float) -> None:
        self.price = price

    async def get_price(self) -> float:
        return self.price


class FailingPriceService:
    async def get_price(self) -> float:  # pragma: no cover - tiny shim
        raise RuntimeError("price service offline")


def test_dashboard_snapshot_renders():
    profile = LoanProfileSettings(
        id="default",
        name="default",
        loan={"principal": 100.0, "interest": 0.0},
        collateral={"btc": 0.5, "usdt": 50.0},
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={"type": "manual"},
        thresholds=Thresholds(),
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    cfg = Config(
        api_keys={},
        loan=profile.loan,
        collateral=profile.collateral,
        thresholds=Thresholds(),
        reserves=profile.reserves,
        policy=profile.policy,
        profiles={"default": profile},
        default_profile="default",
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        ("default", 100.0, 0.0),
    )
    cur.execute(
        "INSERT INTO collateral_snapshot (profile, btc_amount, usdt_amount, btc_price) VALUES (?, ?, ?, ?)",
        ("default", 0.5, 50.0, 200.0),
    )
    conn.commit()

    snapshot = asyncio.run(
        gather_dashboard_snapshot(cfg, price_service=StubPriceService(200.0), conn=conn)
    )

    expected_ltv = 100.0 / (0.5 * 200.0 + 50.0)
    assert pytest.approx(snapshot.ltv, rel=1e-6) == expected_ltv
    assert snapshot.profile_id == "default"
    assert snapshot.status == "safe"
    assert snapshot.margin_buffer_drop_pct is not None
    assert snapshot.terms["platform"] == cfg.terms.platform
    assert snapshot.last_loan_update is not None
    assert snapshot.last_collateral_update is not None

    text = render_dashboard(snapshot)
    assert "Status:" in text
    assert "Margin-call buffer" in text
    assert "Platform terms" in text


def test_dashboard_uses_cached_price_when_live_unavailable():
    profile = LoanProfileSettings(
        id="default",
        name="default",
        loan={"principal": 100.0, "interest": 0.0},
        collateral={"btc": 0.5, "usdt": 50.0},
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={"type": "manual"},
        thresholds=Thresholds(),
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    cfg = Config(
        api_keys={},
        loan=profile.loan,
        collateral=profile.collateral,
        thresholds=Thresholds(),
        reserves=profile.reserves,
        policy=profile.policy,
        profiles={"default": profile},
        default_profile="default",
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        ("default", 100.0, 0.0),
    )
    cur.execute(
        "INSERT INTO collateral_snapshot (profile, btc_amount, usdt_amount, btc_price) VALUES (?, ?, ?, ?)",
        ("default", 0.5, 50.0, 21_000.0),
    )
    cur.execute(
        """
        INSERT INTO ltv_history(
            profile, ltv, btc_amount, usdt_amount, btc_price,
            principal, interest, alert_level
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("default", 0.5, 0.5, 50.0, 21_000.0, 100.0, 0.0, "none"),
    )
    conn.commit()

    snapshot = asyncio.run(
        gather_dashboard_snapshot(cfg, price_service=FailingPriceService(), conn=conn)
    )

    assert snapshot.btc_price == pytest.approx(21_000.0)
    assert any(
        note.startswith("Using cached BTC price") and "ltv history" in note
        for note in snapshot.notes
    )


def test_dashboard_margin_call_recommendation():
    profile = LoanProfileSettings(
        id="default",
        name="default",
        loan={"principal": 85.0, "interest": 0.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={"type": "manual"},
        thresholds=Thresholds(),
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    cfg = Config(
        api_keys={},
        loan=profile.loan,
        collateral=profile.collateral,
        thresholds=Thresholds(),
        reserves=profile.reserves,
        policy=profile.policy,
        profiles={"default": profile},
        default_profile="default",
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        ("default", 85.0, 0.0),
    )
    cur.execute(
        "INSERT INTO collateral_snapshot (profile, btc_amount, usdt_amount, btc_price) VALUES (?, ?, ?, ?)",
        ("default", 1.0, 0.0, 100.0),
    )
    conn.commit()

    snapshot = asyncio.run(
        gather_dashboard_snapshot(cfg, price_service=StubPriceService(100.0), conn=conn)
    )

    assert snapshot.status == "margin_call"
    assert math.isclose(snapshot.margin_buffer_pct, 0.0)
    assert snapshot.margin_buffer_drop_pct == 0.0
    assert "margin-call" in snapshot.recommended_action.lower()


def test_dashboard_switches_between_profiles():
    default_profile = LoanProfileSettings(
        id="default",
        name="default",
        loan={"principal": 200.0, "interest": 0.0},
        collateral={"btc": 1.0, "usdt": 100.0},
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={"type": "manual"},
        thresholds=Thresholds(),
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    experimental = LoanProfileSettings(
        id="experimental",
        name="experimental",
        loan={"principal": 50.0, "interest": 0.0},
        collateral={"btc": 0.25, "usdt": 25.0},
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={"type": "manual"},
        thresholds=Thresholds(margin_call=0.83),
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    cfg = Config(
        api_keys={},
        loan=default_profile.loan,
        collateral=default_profile.collateral,
        thresholds=default_profile.thresholds,
        reserves=default_profile.reserves,
        policy=default_profile.policy,
        profiles={"default": default_profile, "experimental": experimental},
        default_profile="default",
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        [("default", 200.0, 0.0), ("experimental", 50.0, 0.0)],
    )
    cur.executemany(
        "INSERT INTO collateral_snapshot (profile, btc_amount, usdt_amount, btc_price) VALUES (?, ?, ?, ?)",
        [
            ("default", 1.0, 100.0, 250.0),
            ("experimental", 0.25, 25.0, 200.0),
        ],
    )
    conn.commit()

    snapshot = asyncio.run(
        gather_dashboard_snapshot(
            cfg,
            price_service=StubPriceService(200.0),
            conn=conn,
            profile_id="experimental",
        )
    )

    assert snapshot.profile_id == "experimental"
    assert snapshot.thresholds["margin_call"] == pytest.approx(0.83)
    assert snapshot.debt == pytest.approx(50.0)
