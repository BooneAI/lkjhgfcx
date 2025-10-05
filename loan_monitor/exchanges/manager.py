"""Coordinator for multi-exchange collateral operations."""
from __future__ import annotations

import asyncio
import logging
from typing import Dict

from ..config import Config, ExchangeConfig
from ..db import get_connection
from ..services.ltv import LoanState, compute_ltv
from ..services.pricing import PriceService
from .connectors import BaseExchangeConnector, ExchangeBalances, get_connector_class


LOGGER = logging.getLogger(__name__)


class ExchangeManager:
    """Manage multiple exchange connectors and enforce withdrawal guardrails."""

    def __init__(self, config: Config, conn=None, price_service: PriceService | None = None) -> None:
        self.config = config
        self.conn = conn or get_connection()
        self.price_service = price_service or PriceService()
        self.logger = logging.getLogger(__name__)
        self.connectors: Dict[str, Dict[str, BaseExchangeConnector]] = {}
        for exchange in config.exchanges:
            profile_id = config.get_profile(exchange.profile).id
            connector = get_connector_class(exchange.platform)(exchange, conn=self.conn)
            self.connectors.setdefault(profile_id, {})[exchange.name] = connector

    # ------------------------------------------------------------------
    def list_balances(self, profile_id: str | None = None) -> Dict[str, ExchangeBalances]:
        connectors = self._connectors_for(profile_id)
        return {name: connector.get_balances() for name, connector in connectors.items()}

    def aggregate_collateral(self, profile_id: str | None = None) -> dict[str, float]:
        totals = {"btc": 0.0, "usdt": 0.0}
        profile = self.config.get_profile(profile_id)
        connectors = self._connectors_for(profile.id)
        if not connectors:
            totals["btc"] = float(profile.collateral.get("btc", 0.0) or 0.0)
            totals["usdt"] = float(profile.collateral.get("usdt", 0.0) or 0.0)
            return totals
        for connector in connectors.values():
            bal = connector.get_balances()
            totals["btc"] += bal.btc_collateral
            totals["usdt"] += bal.usdt_collateral
        return totals

    def add_collateral(
        self,
        profile_id: str | None,
        name: str,
        asset: str,
        amount: float,
        *,
        user: str | None = None,
    ) -> ExchangeBalances:
        profile = self.config.get_profile(profile_id)
        connector = self._get_connector(profile.id, name)
        balances = connector.add_collateral(asset, amount, user=user)
        price = self._current_price()
        self._record_snapshot(profile.id, price)
        return balances

    def repay(
        self,
        profile_id: str | None,
        name: str,
        amount: float,
        *,
        user: str | None = None,
    ) -> ExchangeBalances:
        profile = self.config.get_profile(profile_id)
        connector = self._get_connector(profile.id, name)
        return connector.repay(amount, user=user)

    def withdraw(
        self,
        profile_id: str | None,
        name: str,
        asset: str,
        amount: float,
        *,
        user: str | None = None,
        confirm: bool = False,
    ) -> dict:
        if not confirm:
            raise ValueError("withdrawal requires explicit --confirm acknowledgement")
        profile = self.config.get_profile(profile_id)
        connector = self._get_connector(profile.id, name)
        cfg = self._get_exchange_config(profile.id, name)
        totals = self.aggregate_collateral(profile.id)
        asset = asset.lower()
        if asset not in totals:
            raise ValueError("unsupported asset")
        if totals[asset] < amount:
            raise ValueError("insufficient aggregated collateral")
        price = self._current_price()
        new_totals = totals.copy()
        new_totals[asset] -= amount
        state = self._loan_state(profile.id, price, new_totals)
        new_ltv = compute_ltv(state)
        margin_threshold = profile.thresholds.margin_call
        safe_threshold = cfg.safe_withdrawal_ltv
        if new_ltv >= margin_threshold:
            raise ValueError("withdrawal would breach margin-call threshold")
        if new_ltv >= safe_threshold:
            raise ValueError(
                f"withdrawal blocked: resulting LTV {new_ltv:.2%} exceeds safe threshold {safe_threshold:.2%}"
            )
        balances = connector.withdraw(asset, amount, user=user)
        self._record_snapshot(profile.id, price)
        return {
            "ltv": new_ltv,
            "totals": new_totals,
            "exchange_balances": balances,
        }

    # ------------------------------------------------------------------
    def _connectors_for(self, profile_id: str | None) -> Dict[str, BaseExchangeConnector]:
        profile = self.config.get_profile(profile_id)
        return self.connectors.get(profile.id, {})

    def _get_connector(self, profile_id: str, name: str) -> BaseExchangeConnector:
        connectors = self._connectors_for(profile_id)
        try:
            return connectors[name]
        except KeyError as exc:  # pragma: no cover - defensive guard
            raise ValueError(f"unknown exchange {name} for profile {profile_id}") from exc

    def _get_exchange_config(self, profile_id: str, name: str) -> ExchangeConfig:
        for exchange in self.config.exchanges:
            if exchange.name == name and self.config.get_profile(exchange.profile).id == profile_id:
                return exchange
        raise ValueError(f"exchange configuration missing for {name} in profile {profile_id}")

    def _current_price(self) -> float:
        return asyncio.run(self.price_service.get_price())

    def _loan_state(self, profile_id: str, price: float, totals: dict[str, float]) -> LoanState:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT principal, interest FROM loan WHERE profile=?",
            (profile_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("loan row missing")
        principal, interest = row
        return LoanState(
            float(principal or 0.0),
            float(interest or 0.0),
            totals.get("btc", 0.0),
            totals.get("usdt", 0.0),
            price,
        )

    def _record_snapshot(self, profile_id: str, btc_price: float) -> None:
        totals = self.aggregate_collateral(profile_id)
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO collateral_snapshot (profile, btc_amount, usdt_amount, btc_price) VALUES (?, ?, ?, ?)",
            (profile_id, totals["btc"], totals["usdt"], btc_price),
        )
        self.conn.commit()


__all__ = ["ExchangeManager"]
