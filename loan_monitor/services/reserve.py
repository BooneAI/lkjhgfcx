from __future__ import annotations

import logging
import time
from typing import Dict

from ..audit import log_audit_event
from ..config import Config
from ..db import get_connection


class ReserveManager:
    """Manage pledged and unpledged asset reserves."""

    def __init__(self, config: Config, profile_id: str | None = None, conn=None) -> None:
        self.config = config
        self.profile = config.get_profile(profile_id)
        self.profile_id = self.profile.id
        self.conn = conn or get_connection()
        self.logger = logging.getLogger(__name__)
        self._last_action = 0.0
        self.cooldown = 3600
        self._ensure_assets()

    def _ensure_assets(self) -> None:
        cur = self.conn.cursor()
        collateral = self.profile.collateral or {}
        reserves = self.profile.reserves or {}
        for asset in ("btc", "usdt"):
            pledged = float(collateral.get(asset, 0.0))
            unpledged = float(reserves.get(asset, 0.0))
            cur.execute(
                "INSERT OR IGNORE INTO reserves(profile, asset, pledged, unpledged) VALUES(?, ?, ?, ?)",
                (self.profile_id, asset, pledged, unpledged),
            )
        self.conn.commit()

    def get_balances(self) -> Dict[str, Dict[str, float]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT asset, pledged, unpledged FROM reserves WHERE profile=?",
            (self.profile_id,),
        )
        rows = cur.fetchall()
        return {asset: {"pledged": p, "unpledged": u} for asset, p, u in rows}

    def transfer(
        self,
        asset: str,
        amount: float,
        to_collateral: bool,
        *,
        user: str | None = None,
    ) -> None:
        if amount < 0:
            raise ValueError("amount must be non-negative")
        cur = self.conn.cursor()
        cur.execute(
            "SELECT pledged, unpledged FROM reserves WHERE profile=? AND asset=?",
            (self.profile_id, asset),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("unknown asset")
        pledged, unpledged = row
        if to_collateral:
            if unpledged < amount:
                raise ValueError("insufficient reserves")
            pledged += amount
            unpledged -= amount
        else:
            if pledged < amount:
                raise ValueError("insufficient pledged collateral")
            pledged -= amount
            unpledged += amount
        cur.execute(
            "UPDATE reserves SET pledged=?, unpledged=? WHERE profile=? AND asset=?",
            (pledged, unpledged, self.profile_id, asset),
        )
        self.conn.commit()
        log_audit_event(
            "reserve.transfer",
            {
                "asset": asset,
                "amount": amount,
                "direction": "to_collateral" if to_collateral else "to_reserve",
            },
            user=user,
            conn=self.conn,
        )

    def apply_policy(self, state) -> None:
        policy = (self.profile.policy or {}).get("type", "manual")
        if policy == "manual":
            return
        if time.time() - self._last_action < self.cooldown:
            return
        if policy == "auto_topup":
            pct = float((self.profile.policy or {}).get("topup_percent", 0.5))
            self._auto_topup(pct)
        elif policy == "auto_repay":
            pct = float((self.profile.policy or {}).get("repay_percent", 0.5))
            self._auto_repay(pct)
        self._last_action = time.time()

    def _auto_topup(self, percent: float) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT asset, unpledged FROM reserves WHERE profile=?",
            (self.profile_id,),
        )
        for asset, unpledged in cur.fetchall():
            amount = unpledged * percent
            if amount > 0:
                self.transfer(
                    asset,
                    amount,
                    to_collateral=True,
                    user="policy:auto_topup",
                )
                self.logger.info("auto topup %s %.8f", asset, amount)

    def _auto_repay(self, percent: float) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT pledged, unpledged FROM reserves WHERE profile=? AND asset='usdt'",
            (self.profile_id,),
        )
        row = cur.fetchone()
        if not row:
            return
        _, unpledged = row
        amount = unpledged * percent
        if amount <= 0:
            return
        cur.execute(
            "SELECT principal FROM loan WHERE profile=?",
            (self.profile_id,),
        )
        loan_row = cur.fetchone()
        if not loan_row:
            return
        principal = loan_row[0]
        repay = min(amount, principal)
        principal -= repay
        unpledged -= repay
        cur.execute(
            "UPDATE loan SET principal=? WHERE profile=?",
            (principal, self.profile_id),
        )
        cur.execute(
            "UPDATE reserves SET unpledged=? WHERE profile=? AND asset='usdt'",
            (unpledged, self.profile_id),
        )
        self.conn.commit()
        log_audit_event(
            "reserve.auto_repay",
            {"amount": repay},
            user="policy:auto_repay",
            conn=self.conn,
        )
        self.logger.info("auto repay %.2f", repay)


__all__ = ["ReserveManager"]
