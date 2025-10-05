from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict

from ..audit import log_audit_event
from ..config import Config
from ..db import get_connection
from ..metrics import get_metrics, init_metrics_server
from ..notifications import Notifier, ConsoleNotifier
from .ltv import LoanState, compute_ltv
from .pricing import PriceService
from .reserve import ReserveManager

_COOLDOWN_SECONDS = 3600


class LTVMonitor:
    """Poll price and loan state to check LTV thresholds."""

    def __init__(
        self,
        config: Config,
        notifier: Notifier | None = None,
        reserve_manager: ReserveManager | None = None,
        conn=None,
        *,
        profile_id: str | None = None,
    ) -> None:
        self.config = config
        self.profile = config.get_profile(profile_id)
        self.profile_id = self.profile.id
        self.notifier = notifier or ConsoleNotifier()
        self.conn = conn or get_connection()
        self.price_service = PriceService()
        self.reserve_manager = reserve_manager or ReserveManager(
            config, profile_id=self.profile_id, conn=self.conn
        )
        self._last_alert: Dict[str, float] = {}
        self.logger = logging.getLogger(f"{__name__}.{self.profile_id}")
        self.metrics = get_metrics()
        if config.observability.enable_metrics:
            try:
                init_metrics_server(config.observability.metrics_port)
            except OSError:
                self.logger.warning("metrics server failed to start", exc_info=True)
        self._ensure_profile_row()

    def _ensure_profile_row(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO loan(profile, principal, interest) VALUES(?, ?, ?)",
            (
                self.profile_id,
                float(self.profile.loan.get("principal", 0.0) or 0.0),
                float(self.profile.loan.get("interest", 0.0) or 0.0),
            ),
        )
        self.conn.commit()

    async def check_once(self) -> float:
        """Check LTV once and send alerts if needed."""
        try:
            price = await self.price_service.get_price()
            cur = self.conn.cursor()
            cur.execute(
                "SELECT principal, interest FROM loan WHERE profile = ?",
                (self.profile_id,),
            )
            loan_row = cur.fetchone()
            if not loan_row:
                self.logger.debug("loan row missing for profile %s", self.profile_id)
                return 0.0
            principal, interest = loan_row
            cur.execute(
                "SELECT SUM(btc_collateral), SUM(usdt_collateral) FROM exchange_positions WHERE profile=?",
                (self.profile_id,),
            )
            exch = cur.fetchone()
            if exch and exch[0] is not None:
                btc_amount = float(exch[0] or 0.0)
                usdt_amount = float(exch[1] or 0.0)
            else:
                cur.execute(
                    "SELECT btc_amount, usdt_amount FROM collateral_snapshot WHERE profile=? ORDER BY id DESC LIMIT 1",
                    (self.profile_id,),
                )
                snap = cur.fetchone()
                if snap:
                    btc_amount, usdt_amount = snap
                else:
                    btc_amount = self.profile.collateral.get("btc", 0.0)
                    usdt_amount = self.profile.collateral.get("usdt", 0.0)
            state = LoanState(principal, interest, btc_amount, usdt_amount, price)
            ltv = compute_ltv(state)
            self.metrics.update_ltv(self.profile_id, ltv)
            level = await self._maybe_alert(ltv, state)
            self._record_history(ltv, state, price, level)
            self.metrics.record_check_success(self.profile_id)
            return ltv
        except Exception:
            self.metrics.record_check_failure(self.profile_id)
            self.logger.exception("monitor check failed")
            return 0.0

    async def _maybe_alert(self, ltv: float, state: LoanState, level: str | None = None) -> str:
        level = level or self._determine_alert_level(ltv)
        if level == "none":
            return "none"
        if self._cooldown_passed(level):
            msg = (
                f"LTV {ltv:.2%} crossed {level.replace('_', ' ')} threshold. "
                f"Collateral ${(state.btc_amount * state.btc_price + state.usdt_amount):.2f}, "
                f"Debt ${(state.principal + state.interest):.2f}"
            )
            await self.notifier.send(level, msg)
            self._last_alert[level] = time.time()
            self.metrics.record_alert(self.profile_id, level)
            if level == "margin_call" and self.reserve_manager:
                self.reserve_manager.apply_policy(state)
            log_audit_event(
                f"ltv_{level}_alert",
                {
                    "ltv": ltv,
                    "collateral_value": state.btc_amount * state.btc_price + state.usdt_amount,
                    "principal": state.principal,
                    "interest": state.interest,
                    "profile": self.profile_id,
                },
                user=None,
                conn=self.conn,
            )
        return level

    def _cooldown_passed(self, level: str) -> bool:
        last = self._last_alert.get(level, 0)
        return time.time() - last > _COOLDOWN_SECONDS

    def _determine_alert_level(self, ltv: float) -> str:
        thresholds = self.profile.thresholds
        if ltv >= thresholds.liquidation:
            return "liquidation"
        if ltv >= thresholds.margin_call:
            return "margin_call"
        if ltv >= thresholds.warning:
            return "warning"
        return "none"

    def _record_history(
        self, ltv: float, state: LoanState, price: float, level: str
    ) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO ltv_history (
                profile, ltv, btc_amount, usdt_amount, btc_price,
                principal, interest, alert_level
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.profile_id,
                float(ltv),
                float(state.btc_amount),
                float(state.usdt_amount),
                float(price),
                float(state.principal),
                float(state.interest),
                level,
            ),
        )
        self.conn.commit()

    async def run_forever(self) -> None:  # pragma: no cover - long running
        while True:
            await self.check_once()
            await asyncio.sleep(self.profile.poll_interval)


__all__ = ["LTVMonitor"]
