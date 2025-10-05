from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..audit import log_audit_event
from ..config import Config
from ..db import get_connection
from .ltv import LoanState, compute_ltv
from .pricing import PriceService
from .reserve import ReserveManager


class RepaymentService:
    """Handle manual loan repayments."""

    def __init__(
        self,
        config: Config,
        conn=None,
        exchange_client=None,
        price_service: PriceService | None = None,
        *,
        profile_id: str | None = None,
    ) -> None:
        self.config = config
        self.profile = config.get_profile(profile_id)
        self.profile_id = self.profile.id
        self.conn = conn or get_connection()
        # ensure reserves table has expected assets
        self.reserve_manager = ReserveManager(config, profile_id=self.profile_id, conn=self.conn)
        self.exchange_client = exchange_client
        self.price_service = price_service or PriceService()
        self.logger = logging.getLogger(__name__)

    def repay(self, amount: float, dry_run: bool = False, *, user: str | None = None) -> dict:
        if amount <= 0:
            raise ValueError("amount must be positive")
        cur = self.conn.cursor()
        cur.execute(
            "SELECT principal, interest FROM loan WHERE profile=?",
            (self.profile_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("loan row missing")
        principal, interest = row
        if amount > principal:
            raise ValueError("repayment exceeds outstanding principal")
        cur.execute(
            "SELECT pledged, unpledged FROM reserves WHERE profile=? AND asset='usdt'",
            (self.profile_id,),
        )
        row = cur.fetchone()
        if not row or row[1] < amount:
            raise ValueError("insufficient USDT reserves")
        pledged_usdt, unpledged_usdt = row
        new_principal = principal - amount
        txid: Optional[str] = None
        if not dry_run:
            if self.exchange_client:
                # pragma: no cover - external API hook
                txid = self.exchange_client.repay(amount)
            cur.execute(
                "UPDATE loan SET principal=? WHERE profile=?",
                (new_principal, self.profile_id),
            )
            cur.execute(
                "UPDATE reserves SET unpledged=? WHERE profile=? AND asset='usdt'",
                (unpledged_usdt - amount, self.profile_id),
            )
            self.conn.commit()
            self.logger.info("repaid %.2f, new principal %.2f", amount, new_principal)
            log_audit_event(
                "loan.repay",
                {
                    "amount": amount,
                    "new_principal": new_principal,
                    "profile": self.profile_id,
                },
                user=user,
                conn=self.conn,
            )
        # compute LTV using pledged balances
        cur.execute(
            "SELECT asset, pledged FROM reserves WHERE profile=?",
            (self.profile_id,),
        )
        rows = cur.fetchall()
        pledged = {asset: p for asset, p in rows}
        btc_amount = pledged.get("btc", 0.0)
        usdt_amount = pledged.get("usdt", 0.0)
        price = asyncio.run(self.price_service.get_price())
        state = LoanState(new_principal, interest, btc_amount, usdt_amount, price)
        ltv = compute_ltv(state)
        return {"principal": new_principal, "ltv": ltv, "txid": txid}


__all__ = ["RepaymentService"]
