import asyncio
import math
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loan_monitor.services.pricing import PriceService
from loan_monitor.services.ltv import LoanState, compute_ltv, price_drop_to_reach_ltv
from loan_monitor.services.monitor import LTVMonitor
from loan_monitor.services.reserve import ReserveManager
from loan_monitor.services.repayment import RepaymentService
from loan_monitor.config import Config, Thresholds, LoanProfileSettings
from loan_monitor.db import get_connection
from loan_monitor.notifications.base import Notifier


class DummyNotifier(Notifier):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send(self, level: str, message: str) -> None:
        self.calls.append(level)


def make_config(
    *,
    loan: dict | None = None,
    collateral: dict | None = None,
    reserves: dict | None = None,
    policy: dict | None = None,
    thresholds: Thresholds | None = None,
    api_keys: dict | None = None,
) -> Config:
    thresholds = thresholds or Thresholds()
    profile = LoanProfileSettings(
        id="default",
        name="default",
        loan=loan or {},
        collateral=collateral or {},
        reserves=reserves or {},
        policy=policy or {},
        thresholds=thresholds,
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    return Config(
        api_keys=api_keys or {},
        loan=profile.loan,
        collateral=profile.collateral,
        thresholds=thresholds,
        reserves=profile.reserves,
        policy=profile.policy,
        profiles={"default": profile},
        default_profile="default",
    )


def test_price_failover(monkeypatch):
    fetcher = PriceService(ttl=0)

    async def bad(*args, **kwargs):
        raise RuntimeError("fail")

    async def good(*args, **kwargs):
        return 42.0

    monkeypatch.setattr(fetcher, "_fetch_binance", bad)
    monkeypatch.setattr(fetcher, "_fetch_coingecko", good)
    price = asyncio.run(fetcher.get_price())
    assert price == 42.0


def test_price_caching(monkeypatch):
    fetcher = PriceService(ttl=1000)
    calls = {"n": 0}

    async def good(*args, **kwargs):
        calls["n"] += 1
        return 50.0

    monkeypatch.setattr(fetcher, "_fetch_binance", good)
    monkeypatch.setattr(fetcher, "_fetch_coingecko", good)
    price1 = asyncio.run(fetcher.get_price())
    price2 = asyncio.run(fetcher.get_price())
    assert price1 == price2 == 50.0
    assert calls["n"] == 1


def test_compute_ltv():
    state = LoanState(100.0, 0.0, 1.0, 50.0, 200.0)
    ltv = compute_ltv(state)
    assert ltv == 100.0 / (1.0 * 200.0 + 50.0)


def test_price_drop_to_reach_ltv():
    state = LoanState(100.0, 0.0, 0.5, 50.0, 200.0)
    drop = price_drop_to_reach_ltv(state, 0.85)
    assert pytest.approx(drop, rel=1e-4) == 0.3235

    inf_state = LoanState(50.0, 0.0, 0.0, 100.0, 200.0)
    assert math.isinf(price_drop_to_reach_ltv(inf_state, 0.85))

    above_state = LoanState(100.0, 0.0, 0.0, 100.0, 200.0)
    assert price_drop_to_reach_ltv(above_state, 0.85) == 0.0

    with pytest.raises(ValueError):
        price_drop_to_reach_ltv(state, 0.0)


def test_alert_throttling(monkeypatch):
    cfg = make_config(loan={}, collateral={}, thresholds=Thresholds())
    notifier = DummyNotifier()
    monitor = LTVMonitor(cfg, notifier=notifier)

    state = LoanState(85.0, 0.0, 1.0, 0.0, 100.0)  # LTV = 0.85 -> margin_call
    ltv = compute_ltv(state)

    asyncio.run(monitor._maybe_alert(ltv, state))
    asyncio.run(monitor._maybe_alert(ltv, state))

    assert notifier.calls == ["margin_call"]


def test_reserve_transfer():
    cfg = make_config(
        loan={},
        collateral={"btc": 0.0, "usdt": 0.0},
        reserves={"btc": 0.1, "usdt": 50.0},
        policy={"type": "manual"},
    )
    conn = get_connection(Path(":memory:"))
    manager = ReserveManager(cfg, conn=conn)
    manager.transfer("btc", 0.05, to_collateral=True)
    balances = manager.get_balances()
    assert balances["btc"]["pledged"] == 0.05
    assert balances["btc"]["unpledged"] == 0.05


def test_auto_topup_policy(monkeypatch):
    cfg = make_config(
        loan={"principal": 85.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        reserves={"btc": 0.1, "usdt": 0.0},
        policy={"type": "auto_topup", "topup_percent": 1.0},
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        ("default", 85.0, 0.0),
    )
    conn.commit()
    manager = ReserveManager(cfg, conn=conn)
    notifier = DummyNotifier()
    monitor = LTVMonitor(cfg, notifier=notifier, reserve_manager=manager, conn=conn)
    state = LoanState(85.0, 0.0, 1.0, 0.0, 100.0)
    ltv = compute_ltv(state)
    asyncio.run(monitor._maybe_alert(ltv, state))
    balances = manager.get_balances()
    assert balances["btc"]["pledged"] == 1.1


def test_repay_reduces_principal_and_ltv(monkeypatch):
    cfg = make_config(
        loan={"principal": 100.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        reserves={"btc": 0.0, "usdt": 50.0},
        policy={"type": "manual"},
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        ("default", 100.0, 0.0),
    )
    conn.commit()
    ReserveManager(cfg, conn=conn)
    service = RepaymentService(cfg, conn=conn)

    async def price():
        return 100.0

    monkeypatch.setattr(service.price_service, "get_price", price)
    result = service.repay(10.0)
    assert result["principal"] == 90.0
    assert pytest.approx(result["ltv"]) == 0.9
    cur.execute("SELECT principal FROM loan WHERE profile='default'")
    assert cur.fetchone()[0] == 90.0
    cur.execute("SELECT unpledged FROM reserves WHERE profile='default' AND asset='usdt'")
    assert cur.fetchone()[0] == 40.0


def test_reserves_are_isolated_per_profile():
    thresholds = Thresholds()
    profile_a = LoanProfileSettings(
        id="default",
        name="default",
        loan={"principal": 0.0},
        collateral={"btc": 0.5, "usdt": 0.0},
        reserves={"btc": 0.1, "usdt": 0.0},
        policy={"type": "manual"},
        thresholds=thresholds,
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    profile_b = LoanProfileSettings(
        id="growth",
        name="growth",
        loan={"principal": 0.0},
        collateral={"btc": 0.2, "usdt": 0.0},
        reserves={"btc": 0.05, "usdt": 0.0},
        policy={"type": "manual"},
        thresholds=thresholds,
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    cfg = Config(
        api_keys={},
        loan=profile_a.loan,
        collateral=profile_a.collateral,
        thresholds=thresholds,
        reserves=profile_a.reserves,
        policy=profile_a.policy,
        profiles={"default": profile_a, "growth": profile_b},
        default_profile="default",
    )
    conn = get_connection(Path(":memory:"))
    manager_default = ReserveManager(cfg, conn=conn)
    manager_growth = ReserveManager(cfg, profile_id="growth", conn=conn)

    manager_default.transfer("btc", 0.05, to_collateral=True)
    manager_growth.transfer("btc", 0.02, to_collateral=True)

    balances_default = manager_default.get_balances()
    balances_growth = manager_growth.get_balances()

    assert pytest.approx(balances_default["btc"]["pledged"]) == 0.55
    assert pytest.approx(balances_default["btc"]["unpledged"]) == 0.05
    assert pytest.approx(balances_growth["btc"]["pledged"]) == 0.22
    assert pytest.approx(balances_growth["btc"]["unpledged"]) == 0.03


def test_monitor_uses_profile_specific_thresholds(monkeypatch):
    thresholds_default = Thresholds(warning=0.8, margin_call=0.9, liquidation=0.95)
    thresholds_growth = Thresholds(warning=0.75, margin_call=0.82, liquidation=0.9)
    profile_a = LoanProfileSettings(
        id="default",
        name="default",
        loan={"principal": 0.0},
        collateral={"btc": 0.0, "usdt": 0.0},
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={"type": "manual"},
        thresholds=thresholds_default,
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    profile_b = LoanProfileSettings(
        id="growth",
        name="growth",
        loan={"principal": 80.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={"type": "manual"},
        thresholds=thresholds_growth,
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    cfg = Config(
        api_keys={},
        loan=profile_a.loan,
        collateral=profile_a.collateral,
        thresholds=thresholds_default,
        reserves=profile_a.reserves,
        policy=profile_a.policy,
        profiles={"default": profile_a, "growth": profile_b},
        default_profile="default",
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        ("growth", 80.0, 0.0),
    )
    cur.execute(
        "INSERT INTO collateral_snapshot (profile, btc_amount, usdt_amount, btc_price) VALUES (?, ?, ?, ?)",
        ("growth", 1.0, 0.0, 100.0),
    )
    conn.commit()

    notifier = DummyNotifier()
    monitor = LTVMonitor(cfg, notifier=notifier, profile_id="growth", conn=conn)
    state = LoanState(80.0, 0.0, 1.0, 0.0, 95.0)
    ltv = compute_ltv(state)

    asyncio.run(monitor._maybe_alert(ltv, state))
    assert notifier.calls == ["margin_call"]
    assert monitor.profile_id == "growth"


def test_reserve_transfer_emits_audit(tmp_path):
    cfg = make_config(
        loan={"principal": 0.0},
        collateral={"btc": 1.0, "usdt": 200.0},
        reserves={"btc": 0.0, "usdt": 50.0},
        policy={"type": "manual"},
    )
    conn = get_connection(tmp_path / "db.sqlite")
    manager = ReserveManager(cfg, conn=conn)
    manager.transfer("usdt", 10.0, to_collateral=True, user="alice")
    cur = conn.cursor()
    cur.execute(
        "SELECT action, user, details FROM audit_log WHERE action='reserve.transfer' ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()
    assert row is not None
    assert row[1] == "alice"
    assert "to_collateral" in row[2]


def test_repay_dry_run(monkeypatch):
    cfg = make_config(
        loan={"principal": 100.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        reserves={"btc": 0.0, "usdt": 50.0},
        policy={"type": "manual"},
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        ("default", 100.0, 0.0),
    )
    conn.commit()
    ReserveManager(cfg, conn=conn)
    service = RepaymentService(cfg, conn=conn)

    async def price():
        return 100.0

    monkeypatch.setattr(service.price_service, "get_price", price)
    result = service.repay(10.0, dry_run=True)
    assert result["principal"] == 90.0
    cur.execute("SELECT principal FROM loan WHERE profile='default'")
    assert cur.fetchone()[0] == 100.0
    cur.execute("SELECT unpledged FROM reserves WHERE profile='default' AND asset='usdt'")
    assert cur.fetchone()[0] == 50.0


def test_repay_validation(monkeypatch):
    cfg = make_config(
        loan={"principal": 100.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        reserves={"btc": 0.0, "usdt": 50.0},
        policy={"type": "manual"},
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        ("default", 100.0, 0.0),
    )
    conn.commit()
    ReserveManager(cfg, conn=conn)
    service = RepaymentService(cfg, conn=conn)

    async def price():
        return 100.0

    monkeypatch.setattr(service.price_service, "get_price", price)
    with pytest.raises(ValueError):
        service.repay(-1.0)
    with pytest.raises(ValueError):
        service.repay(0.0)
    with pytest.raises(ValueError):
        service.repay(200.0)
    with pytest.raises(ValueError):
        service.repay(60.0)
