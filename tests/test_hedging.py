import asyncio
from pathlib import Path

import pytest

from loan_monitor.config import Config, HedgingSettings, KnowledgeQuestion, Thresholds, LoanProfileSettings
from loan_monitor.db import get_connection
from loan_monitor.services.hedging import HedgingConsentError, HedgingService


@pytest.fixture()
def hedging_config():
    questions = [KnowledgeQuestion(id="risk", prompt="Max loss?", correct_answers=["premium"])]
    settings = HedgingSettings(
        enabled=True,
        require_consent=True,
        consent_prompt="",
        knowledge_check=questions,
        allowed_strategies=["put_option", "short_futures"],
        paper_trading=True,
        default_implied_vol=0.5,
        risk_free_rate=0.01,
        futures_margin_ratio=0.2,
        max_notional=100000.0,
    )
    profile = LoanProfileSettings(
        id="default",
        name="default",
        loan={},
        collateral={},
        reserves={},
        policy={},
        thresholds=Thresholds(),
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    return Config(
        api_keys={},
        loan=profile.loan,
        collateral=profile.collateral,
        thresholds=profile.thresholds,
        policy=profile.policy,
        reserves=profile.reserves,
        hedging=settings,
        profiles={"default": profile},
        default_profile="default",
    )


@pytest.fixture()
def hedging_service(hedging_config, monkeypatch):
    conn = get_connection(Path(":memory:"))
    service = HedgingService(hedging_config, conn=conn)

    async def fake_price():
        return 20000.0

    monkeypatch.setattr(service.price_service, "get_price", fake_price)
    return service


def test_quote_requires_consent(hedging_service):
    with pytest.raises(HedgingConsentError):
        asyncio.run(
            hedging_service.quote_put_option(
                "trader",
                strike=25000.0,
                expiry_days=30,
                size=1.0,
            )
        )


def test_record_consent_and_quote(hedging_service):
    hedging_service.record_consent("trader", {"risk": "Premium"}, acknowledge=True)
    quote = asyncio.run(
        hedging_service.quote_put_option(
            "trader",
            strike=25000.0,
            expiry_days=30,
            size=1.0,
        )
    )
    assert quote.premium > 0
    payoff = hedging_service.payoff_profile(quote, points=5)
    assert len(payoff) == 5
    audit = hedging_service.audit_log(limit=5)
    assert any(entry["action"] == "quote_put_option" for entry in audit)


def test_execute_trade_requires_confirmation(hedging_service):
    hedging_service.record_consent("trader", {"risk": "premium"}, acknowledge=True)
    quote = asyncio.run(
        hedging_service.quote_short_futures(
            "trader",
            size=0.5,
        )
    )
    with pytest.raises(ValueError):
        asyncio.run(hedging_service.execute_trade("trader", quote, confirm=False))
    result = asyncio.run(hedging_service.execute_trade("trader", quote, confirm=True))
    assert result["mode"] == "paper"
    audit = hedging_service.audit_log(limit=2)
    assert any(entry["action"] == "execute_trade" for entry in audit)


def test_invalid_consent_answers(hedging_service):
    with pytest.raises(ValueError):
        hedging_service.record_consent("trader", {"risk": "wrong"}, acknowledge=True)


def test_live_trade_requires_api(monkeypatch):
    questions = [KnowledgeQuestion(id="risk", prompt="Max loss?", correct_answers=["premium"])]
    settings = HedgingSettings(
        enabled=True,
        require_consent=True,
        consent_prompt="",
        knowledge_check=questions,
        allowed_strategies=["put_option", "short_futures"],
        paper_trading=False,
        default_implied_vol=0.5,
        risk_free_rate=0.01,
        futures_margin_ratio=0.2,
        max_notional=100000.0,
    )
    profile = LoanProfileSettings(
        id="default",
        name="default",
        loan={},
        collateral={},
        reserves={},
        policy={},
        thresholds=Thresholds(),
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    cfg = Config(
        api_keys={},
        loan=profile.loan,
        collateral=profile.collateral,
        thresholds=profile.thresholds,
        policy=profile.policy,
        reserves=profile.reserves,
        hedging=settings,
        profiles={"default": profile},
        default_profile="default",
    )
    conn = get_connection(Path(":memory:"))
    service = HedgingService(cfg, conn=conn)

    async def fake_price():
        return 20000.0

    monkeypatch.setattr(service.price_service, "get_price", fake_price)
    service.record_consent("trader", {"risk": "premium"}, acknowledge=True)
    quote = asyncio.run(service.quote_short_futures("trader", size=1.0))
    with pytest.raises(RuntimeError):
        asyncio.run(service.execute_trade("trader", quote, confirm=True))
