"""Exchange connector abstractions for multi-platform collateral management."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..audit import log_audit_event
from ..config import ExchangeConfig
from ..db import get_connection


@dataclass
class ExchangeBalances:
    btc_collateral: float
    usdt_collateral: float
    loan_outstanding: float


class BaseExchangeConnector:
    """Simple stateful connector backed by the local database."""

    platform: str = "generic"

    def __init__(self, config: ExchangeConfig, conn=None) -> None:
        self.config = config
        self.profile_id = config.profile
        self.conn = conn or get_connection()
        self.logger = logging.getLogger(f"{__name__}.{config.name}")
        self._ensure_row()

    # -- public API -----------------------------------------------------
    def get_balances(self) -> ExchangeBalances:
        row = self._fetch_row()
        return ExchangeBalances(*row)

    def add_collateral(self, asset: str, amount: float, *, user: str | None = None) -> ExchangeBalances:
        if amount <= 0:
            raise ValueError("amount must be positive")
        asset = asset.lower()
        if asset not in {"btc", "usdt"}:
            raise ValueError("unsupported asset")
        btc, usdt, loan = self._fetch_row()
        if asset == "btc":
            btc += amount
        else:
            usdt += amount
        self._persist(btc, usdt, loan)
        log_audit_event(
            "exchange.add_collateral",
            {
                "exchange": self.config.name,
                "profile": self.profile_id,
                "asset": asset,
                "amount": amount,
            },
            user=user,
            conn=self.conn,
        )
        return ExchangeBalances(btc, usdt, loan)

    def repay(self, amount: float, *, user: str | None = None) -> ExchangeBalances:
        if amount <= 0:
            raise ValueError("amount must be positive")
        btc, usdt, loan = self._fetch_row()
        if amount > loan:
            raise ValueError("repayment exceeds outstanding loan")
        loan -= amount
        self._persist(btc, usdt, loan)
        log_audit_event(
            "exchange.repay",
            {
                "exchange": self.config.name,
                "profile": self.profile_id,
                "amount": amount,
            },
            user=user,
            conn=self.conn,
        )
        return ExchangeBalances(btc, usdt, loan)

    def withdraw(self, asset: str, amount: float, *, user: str | None = None) -> ExchangeBalances:
        if amount <= 0:
            raise ValueError("amount must be positive")
        asset = asset.lower()
        if asset not in {"btc", "usdt"}:
            raise ValueError("unsupported asset")
        btc, usdt, loan = self._fetch_row()
        if asset == "btc":
            if amount > btc:
                raise ValueError("insufficient BTC collateral")
            btc -= amount
        else:
            if amount > usdt:
                raise ValueError("insufficient USDT collateral")
            usdt -= amount
        self._persist(btc, usdt, loan)
        log_audit_event(
            "exchange.withdraw",
            {
                "exchange": self.config.name,
                "profile": self.profile_id,
                "asset": asset,
                "amount": amount,
            },
            user=user,
            conn=self.conn,
        )
        return ExchangeBalances(btc, usdt, loan)

    # -- helpers --------------------------------------------------------
    def _ensure_row(self) -> None:
        collateral = self.config.collateral or {}
        btc = float(collateral.get("btc", 0.0) or 0.0)
        usdt = float(collateral.get("usdt", 0.0) or 0.0)
        loan = float(self.config.loan_outstanding or 0.0)
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO exchange_positions(profile, name, platform, btc_collateral, usdt_collateral, loan_outstanding) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (self.profile_id, self.config.name, self.config.platform, btc, usdt, loan),
        )
        cur.execute(
            "UPDATE exchange_positions SET platform=?, last_sync=CURRENT_TIMESTAMP WHERE profile=? AND name=?",
            (self.config.platform, self.profile_id, self.config.name),
        )
        self.conn.commit()

    def _fetch_row(self) -> tuple[float, float, float]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT btc_collateral, usdt_collateral, loan_outstanding FROM exchange_positions WHERE profile=? AND name=?",
            (self.profile_id, self.config.name),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("exchange missing")
        return float(row[0]), float(row[1]), float(row[2])

    def _persist(self, btc: float, usdt: float, loan: float) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE exchange_positions SET btc_collateral=?, usdt_collateral=?, loan_outstanding=?, last_sync=CURRENT_TIMESTAMP "
            "WHERE profile=? AND name=?",
            (btc, usdt, loan, self.profile_id, self.config.name),
        )
        self.conn.commit()


class BinanceConnector(BaseExchangeConnector):
    platform = "binance"


class NexoConnector(BaseExchangeConnector):
    platform = "nexo"


PLATFORM_CONNECTORS = {
    "binance": BinanceConnector,
    "nexo": NexoConnector,
}


def get_connector_class(platform: str) -> type[BaseExchangeConnector]:
    return PLATFORM_CONNECTORS.get(platform.lower(), BaseExchangeConnector)


__all__ = [
    "ExchangeBalances",
    "BaseExchangeConnector",
    "BinanceConnector",
    "NexoConnector",
    "get_connector_class",
]
