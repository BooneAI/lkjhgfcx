from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict

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
    ) -> None:
        self.config = config
        self.notifier = notifier or ConsoleNotifier()
        self.conn = conn or get_connection()
        self.price_service = PriceService()
        self.reserve_manager = reserve_manager or ReserveManager(config, conn=self.conn)
        self._last_alert: Dict[str, float] = {}
        self.logger = logging.getLogger(__name__)
        self.metrics = get_metrics()
        if config.observability.enable_metrics:
            try:
                init_metrics_server(config.observability.metrics_port)
            except OSError:
                self.logger.warning("metrics server failed to start", exc_info=True)

    async def check_once(self) -> float:
        """Check LTV once and send alerts if needed."""
        try:
            price = await self.price_service.get_price()
            cur = self.conn.cursor()
            cur.execute("SELECT principal, interest FROM loan WHERE id = 1")
            loan_row = cur.fetchone()
            if not loan_row:
                self.logger.debug("loan row missing")
                return 0.0
            principal, interest = loan_row
            cur.execute(
                "SELECT btc_amount, usdt_amount FROM collateral_snapshot ORDER BY id DESC LIMIT 1"
            )
            snap = cur.fetchone()
            if snap:
                btc_amount, usdt_amount = snap
            else:
                btc_amount = self.config.collateral.get("btc", 0.0)
                usdt_amount = self.config.collateral.get("usdt", 0.0)
            state = LoanState(principal, interest, btc_amount, usdt_amount, price)
            ltv = compute_ltv(state)
            self.metrics.update_ltv(ltv)
            await self._maybe_alert(ltv, state)
            return ltv
        except Exception:
            self.metrics.record_check_failure()
            self.logger.exception("monitor check failed")
            return 0.0

    async def _maybe_alert(self, ltv: float, state: LoanState) -> None:
        thresholds = self.config.thresholds
        levels = [
            (thresholds.liquidation, "liquidation"),
            (thresholds.margin_call, "margin_call"),
            (thresholds.warning, "warning"),
        ]
        for limit, name in levels:
            if ltv >= limit:
                if self._cooldown_passed(name):
                    msg = (
                        f"LTV {ltv:.2%} crossed {name.replace('_', ' ')} threshold. "
                        f"Collateral ${(state.btc_amount * state.btc_price + state.usdt_amount):.2f}, "
                        f"Debt ${(state.principal + state.interest):.2f}"
                    )
                    await self.notifier.send(name, msg)
                    self._last_alert[name] = time.time()
                    self.metrics.record_alert(name)
                    if name == "margin_call" and self.reserve_manager:
                        self.reserve_manager.apply_policy(state)
                break

    def _cooldown_passed(self, level: str) -> bool:
        last = self._last_alert.get(level, 0)
        return time.time() - last > _COOLDOWN_SECONDS

    async def run_forever(self) -> None:  # pragma: no cover - long running
        while True:
            await self.check_once()
            await asyncio.sleep(self.config.poll_interval)


__all__ = ["LTVMonitor"]
