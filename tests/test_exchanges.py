import pytest

from loan_monitor.config import (
    Config,
    Thresholds,
    Terms,
    ObservabilitySettings,
    HedgingSettings,
    SecuritySettings,
    ExchangeConfig,
    LoanProfileSettings,
)
from loan_monitor.db import get_connection
from loan_monitor.exchanges.manager import ExchangeManager


class StubPriceService:
    def __init__(self, price: float) -> None:
        self.price = price

    async def get_price(self) -> float:
        return self.price


def build_config(*, safe_threshold: float = 0.30) -> Config:
    thresholds = Thresholds(warning=0.8, margin_call=0.85, liquidation=0.91)
    profile = LoanProfileSettings(
        id="default",
        name="default",
        loan={"principal": 1000.0, "interest": 0.0},
        collateral={"btc": 0.4, "usdt": 225.0},
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={},
        thresholds=thresholds,
        poll_interval=600,
        notification_channels=[],
        exchanges=["binance-main", "nexo-yield"],
    )
    return Config(
        api_keys={},
        loan=profile.loan,
        collateral=profile.collateral,
        thresholds=thresholds,
        poll_interval=600,
        notification_channels=[],
        reserves=profile.reserves,
        policy=profile.policy,
        terms=Terms(),
        security=SecuritySettings(jwt_secret="secret", users={}),
        observability=ObservabilitySettings(),
        hedging=HedgingSettings(),
        exchanges=[
            ExchangeConfig(
                name="binance-main",
                platform="binance",
                collateral={"btc": 0.25, "usdt": 150.0},
                loan_outstanding=500.0,
                safe_withdrawal_ltv=safe_threshold,
                profile="default",
            ),
            ExchangeConfig(
                name="nexo-yield",
                platform="nexo",
                collateral={"btc": 0.15, "usdt": 75.0},
                loan_outstanding=250.0,
                safe_withdrawal_ltv=0.35,
                profile="default",
            ),
        ],
        profiles={"default": profile},
        default_profile="default",
    )


def build_multi_profile_config() -> Config:
    thresholds_default = Thresholds(warning=0.8, margin_call=0.85, liquidation=0.91)
    thresholds_alt = Thresholds(warning=0.79, margin_call=0.84, liquidation=0.9)
    core = LoanProfileSettings(
        id="default",
        name="core",
        loan={"principal": 1000.0, "interest": 0.0},
        collateral={"btc": 0.4, "usdt": 225.0},
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={},
        thresholds=thresholds_default,
        poll_interval=600,
        notification_channels=[],
        exchanges=["binance-main", "nexo-yield"],
    )
    hedge = LoanProfileSettings(
        id="hedge",
        name="hedge",
        loan={"principal": 200.0, "interest": 0.0},
        collateral={"btc": 0.1, "usdt": 50.0},
        reserves={"btc": 0.0, "usdt": 0.0},
        policy={},
        thresholds=thresholds_alt,
        poll_interval=600,
        notification_channels=[],
        exchanges=["deribit-hedge"],
    )
    return Config(
        api_keys={},
        loan=core.loan,
        collateral=core.collateral,
        thresholds=thresholds_default,
        poll_interval=600,
        notification_channels=[],
        reserves=core.reserves,
        policy=core.policy,
        terms=Terms(),
        security=SecuritySettings(jwt_secret="secret", users={}),
        observability=ObservabilitySettings(),
        hedging=HedgingSettings(),
        exchanges=[
            ExchangeConfig(
                name="binance-main",
                platform="binance",
                collateral={"btc": 0.25, "usdt": 150.0},
                loan_outstanding=500.0,
                safe_withdrawal_ltv=0.30,
                profile="default",
            ),
            ExchangeConfig(
                name="nexo-yield",
                platform="nexo",
                collateral={"btc": 0.15, "usdt": 75.0},
                loan_outstanding=250.0,
                safe_withdrawal_ltv=0.35,
                profile="default",
            ),
            ExchangeConfig(
                name="deribit-hedge",
                platform="deribit",
                collateral={"btc": 0.1, "usdt": 25.0},
                loan_outstanding=100.0,
                safe_withdrawal_ltv=0.28,
                profile="hedge",
            ),
        ],
        profiles={"default": core, "hedge": hedge},
        default_profile="default",
    )


def init_manager(tmp_path, price: float = 20000.0, *, safe_threshold: float = 0.30) -> tuple[ExchangeManager, Config]:
    cfg = build_config(safe_threshold=safe_threshold)
    conn = get_connection(tmp_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        ("default", 1000.0, 0.0),
    )
    conn.commit()
    manager = ExchangeManager(cfg, conn=conn, price_service=StubPriceService(price))
    return manager, cfg


def test_withdraw_requires_confirmation(tmp_path):
    manager, _ = init_manager(tmp_path / "db.sqlite")
    with pytest.raises(ValueError):
        manager.withdraw("default", "binance-main", "btc", 0.01, user="alice", confirm=False)


def test_withdraw_blocks_above_safe_threshold(tmp_path):
    manager, _ = init_manager(tmp_path / "db.sqlite", safe_threshold=0.20)
    with pytest.raises(ValueError) as exc:
        manager.withdraw("default", "binance-main", "btc", 0.2, user="alice", confirm=True)
    assert "safe threshold" in str(exc.value)


def test_successful_withdraw_updates_totals_and_audit(tmp_path):
    db_path = tmp_path / "db.sqlite"
    manager, cfg = init_manager(db_path)
    result = manager.withdraw("default", "binance-main", "btc", 0.01, user="alice", confirm=True)
    assert result["ltv"] < cfg.thresholds.warning
    assert pytest.approx(result["totals"]["btc"], rel=1e-3) == 0.39
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT action, user, details FROM audit_log WHERE action='exchange.withdraw' ORDER BY id DESC LIMIT 1"
    )
    audit_row = cur.fetchone()
    assert audit_row is not None
    assert audit_row[1] == "alice"
    assert "binance-main" in audit_row[2]
    cur.execute("SELECT btc_amount, usdt_amount FROM collateral_snapshot ORDER BY id DESC LIMIT 1")
    snap = cur.fetchone()
    assert snap is not None
    assert pytest.approx(snap[0], rel=1e-3) == result["totals"]["btc"]


def test_list_balances_scoped_per_profile(tmp_path):
    db_path = tmp_path / "profiles.sqlite"
    cfg = build_multi_profile_config()
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO loan (profile, principal, interest) VALUES (?, ?, ?)",
        [("default", 1000.0, 0.0), ("hedge", 200.0, 0.0)],
    )
    conn.commit()
    manager = ExchangeManager(cfg, conn=conn, price_service=StubPriceService(20000.0))

    default_balances = manager.list_balances()
    hedge_balances = manager.list_balances(profile_id="hedge")

    assert set(default_balances) == {"binance-main", "nexo-yield"}
    assert set(hedge_balances) == {"deribit-hedge"}

    totals = manager.aggregate_collateral(profile_id="hedge")
    assert pytest.approx(totals["btc"], rel=1e-3) == 0.1
    assert pytest.approx(totals["usdt"], rel=1e-3) == 25.0
