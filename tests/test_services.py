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
from loan_monitor.config import Config, Thresholds
from loan_monitor.db import get_connection
from loan_monitor.notifications.base import Notifier


class DummyNotifier(Notifier):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send(self, level: str, message: str) -> None:
        self.calls.append(level)


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
    cfg = Config(api_keys={}, loan={}, collateral={}, thresholds=Thresholds())
    notifier = DummyNotifier()
    monitor = LTVMonitor(cfg, notifier=notifier)

    state = LoanState(85.0, 0.0, 1.0, 0.0, 100.0)  # LTV = 0.85 -> margin_call
    ltv = compute_ltv(state)

    asyncio.run(monitor._maybe_alert(ltv, state))
    asyncio.run(monitor._maybe_alert(ltv, state))

    assert notifier.calls == ["margin_call"]


def test_reserve_transfer():
    cfg = Config(
        api_keys={},
        loan={},
        collateral={"btc": 0.0, "usdt": 0.0},
        reserves={"btc": 0.1, "usdt": 50.0},
        thresholds=Thresholds(),
        policy={"type": "manual"},
    )
    conn = get_connection(Path(":memory:"))
    manager = ReserveManager(cfg, conn=conn)
    manager.transfer("btc", 0.05, to_collateral=True)
    balances = manager.get_balances()
    assert balances["btc"]["pledged"] == 0.05
    assert balances["btc"]["unpledged"] == 0.05


def test_auto_topup_policy(monkeypatch):
    cfg = Config(
        api_keys={},
        loan={"principal": 85.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        reserves={"btc": 0.1, "usdt": 0.0},
        thresholds=Thresholds(),
        policy={"type": "auto_topup", "topup_percent": 1.0},
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute("INSERT INTO loan (id, principal, interest) VALUES (1, 85.0, 0.0)")
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
    cfg = Config(
        api_keys={},
        loan={"principal": 100.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        reserves={"btc": 0.0, "usdt": 50.0},
        thresholds=Thresholds(),
        policy={"type": "manual"},
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute("INSERT INTO loan (id, principal, interest) VALUES (1, 100.0, 0.0)")
    conn.commit()
    ReserveManager(cfg, conn=conn)
    service = RepaymentService(cfg, conn=conn)

    async def price():
        return 100.0

    monkeypatch.setattr(service.price_service, "get_price", price)
    result = service.repay(10.0)
    assert result["principal"] == 90.0
    assert pytest.approx(result["ltv"]) == 0.9
    cur.execute("SELECT principal FROM loan WHERE id=1")
    assert cur.fetchone()[0] == 90.0
    cur.execute("SELECT unpledged FROM reserves WHERE asset='usdt'")
    assert cur.fetchone()[0] == 40.0


def test_repay_dry_run(monkeypatch):
    cfg = Config(
        api_keys={},
        loan={"principal": 100.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        reserves={"btc": 0.0, "usdt": 50.0},
        thresholds=Thresholds(),
        policy={"type": "manual"},
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute("INSERT INTO loan (id, principal, interest) VALUES (1, 100.0, 0.0)")
    conn.commit()
    ReserveManager(cfg, conn=conn)
    service = RepaymentService(cfg, conn=conn)

    async def price():
        return 100.0

    monkeypatch.setattr(service.price_service, "get_price", price)
    result = service.repay(10.0, dry_run=True)
    assert result["principal"] == 90.0
    cur.execute("SELECT principal FROM loan WHERE id=1")
    assert cur.fetchone()[0] == 100.0
    cur.execute("SELECT unpledged FROM reserves WHERE asset='usdt'")
    assert cur.fetchone()[0] == 50.0


def test_repay_validation(monkeypatch):
    cfg = Config(
        api_keys={},
        loan={"principal": 100.0},
        collateral={"btc": 1.0, "usdt": 0.0},
        reserves={"btc": 0.0, "usdt": 50.0},
        thresholds=Thresholds(),
        policy={"type": "manual"},
    )
    conn = get_connection(Path(":memory:"))
    cur = conn.cursor()
    cur.execute("INSERT INTO loan (id, principal, interest) VALUES (1, 100.0, 0.0)")
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
