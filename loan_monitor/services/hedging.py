"""Optional hedging utilities with consent and payoff analysis."""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence
import sqlite3

from ..config import Config
from ..db import get_connection
from .pricing import PriceService


class HedgingDisabledError(RuntimeError):
    """Raised when hedging features are disabled in configuration."""


class HedgingConsentError(RuntimeError):
    """Raised when a user attempts hedging actions without consent."""


@dataclass(slots=True)
class PayoffPoint:
    price: float
    pnl: float


@dataclass(slots=True)
class HedgeQuote:
    strategy: str
    size: float
    underlying: float
    premium: float
    cost: float
    source: str
    strike: float | None = None
    expiry_days: int | None = None
    breakeven: float | None = None
    max_profit: float | None = None
    max_loss: float | None = None
    implied_vol: float | None = None
    entry_price: float | None = None
    summary: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class HedgingService:
    """Provide hedging quotes, payoff analysis, and execution logging."""

    def __init__(
        self,
        cfg: Config,
        *,
        conn: sqlite3.Connection | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.settings = cfg.hedging
        self.conn = conn or get_connection(db_path)
        self.price_service = PriceService()

    # ------------------------------------------------------------------
    # Consent management
    # ------------------------------------------------------------------
    def has_consent(self, user: str) -> bool:
        if not self.settings.require_consent:
            return True
        cur = self.conn.cursor()
        cur.execute(
            "SELECT 1 FROM hedging_consent WHERE user=? AND version=?",
            (user, self.settings.consent_version),
        )
        return cur.fetchone() is not None

    def record_consent(
        self,
        user: str,
        answers: dict[str, str],
        *,
        acknowledge: bool,
    ) -> None:
        if not self.settings.enabled:
            raise HedgingDisabledError("hedging module disabled")
        if self.settings.require_consent and not acknowledge:
            raise ValueError("explicit acknowledgement required")
        self._validate_answers(answers)
        payload = json.dumps(answers, sort_keys=True)
        cur = self.conn.cursor()
        cur.execute(
            "REPLACE INTO hedging_consent(user, version, answers) VALUES(?,?,?)",
            (user, self.settings.consent_version, payload),
        )
        self.conn.commit()
        self._log(user, "consent_recorded", {"version": self.settings.consent_version})

    # ------------------------------------------------------------------
    # Quoting helpers
    # ------------------------------------------------------------------
    async def quote_put_option(
        self,
        user: str,
        *,
        strike: float,
        expiry_days: int,
        size: float,
        implied_vol: float | None = None,
        underlying: float | None = None,
    ) -> HedgeQuote:
        self._ensure_enabled("put_option")
        self._require_consent(user)
        if strike <= 0:
            raise ValueError("strike must be positive")
        if expiry_days <= 0:
            raise ValueError("expiry_days must be positive")
        if size <= 0:
            raise ValueError("size must be positive")
        spot = underlying if underlying is not None else await self.price_service.get_price()
        remote = await self._fetch_option_quote(strike=strike, expiry_days=expiry_days, size=size)
        if remote:
            premium = float(remote.get("premium", 0.0))
            source = "remote"
            implied = remote.get("implied_vol")
            if implied is not None:
                implied_vol = float(implied)
        else:
            implied_vol = implied_vol if implied_vol is not None else self.settings.default_implied_vol
            premium = self._black_scholes_put(
                spot,
                strike,
                self.settings.risk_free_rate,
                implied_vol,
                expiry_days / 365.0,
            )
            source = "fallback"
        premium = max(premium, 0.0)
        cost = premium * size
        breakeven = strike - premium
        max_profit = max(strike - 0.0, 0.0) * size - cost
        max_loss = cost
        summary = (
            f"Long put caps downside below ${strike:,.0f}. Breakeven ${breakeven:,.2f}; "
            f"max loss ${max_loss:,.2f}."
        )
        quote = HedgeQuote(
            strategy="put_option",
            size=size,
            underlying=spot,
            premium=premium,
            cost=cost,
            source=source,
            strike=strike,
            expiry_days=expiry_days,
            breakeven=breakeven,
            max_profit=max_profit,
            max_loss=max_loss,
            implied_vol=implied_vol,
            entry_price=None,
            summary=summary,
        )
        self._log(user, "quote_put_option", quote.to_dict())
        return quote

    async def quote_short_futures(
        self,
        user: str,
        *,
        size: float,
        entry_price: float | None = None,
    ) -> HedgeQuote:
        self._ensure_enabled("short_futures")
        self._require_consent(user)
        if size <= 0:
            raise ValueError("size must be positive")
        spot = entry_price if entry_price is not None else await self.price_service.get_price()
        remote = await self._fetch_futures_quote(size=size)
        if remote:
            spot = float(remote.get("entry_price", spot))
            source = "remote"
        else:
            source = "fallback"
        margin_ratio = max(self.settings.futures_margin_ratio, 0.0)
        cost = spot * size * margin_ratio
        max_profit = spot * size
        summary = (
            f"Short futures earns ${size:,.4f} per $1 BTC drop. Margin requirement ${cost:,.2f}."
        )
        quote = HedgeQuote(
            strategy="short_futures",
            size=size,
            underlying=spot,
            premium=0.0,
            cost=cost,
            source=source,
            strike=None,
            expiry_days=None,
            breakeven=spot,
            max_profit=max_profit,
            max_loss=None,
            implied_vol=None,
            entry_price=spot,
            summary=summary,
        )
        self._log(user, "quote_short_futures", quote.to_dict())
        return quote

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------
    def payoff_profile(
        self,
        quote: HedgeQuote,
        *,
        price_range: Sequence[float] | None = None,
        points: int = 25,
    ) -> list[PayoffPoint]:
        if points < 2:
            raise ValueError("points must be at least 2")
        start, end = self._resolve_price_range(quote, price_range)
        step = (end - start) / (points - 1)
        payoff: list[PayoffPoint] = []
        for i in range(points):
            price = start + step * i
            pnl = self._calculate_pnl(quote, price)
            payoff.append(PayoffPoint(price=price, pnl=pnl))
        return payoff

    def plot_payoff(
        self,
        quote: HedgeQuote,
        target: str | Path,
        *,
        price_range: Sequence[float] | None = None,
        points: int = 25,
    ) -> None:
        data = self.payoff_profile(quote, price_range=price_range, points=points)
        import matplotlib

        matplotlib.use("Agg", force=True)
        from matplotlib import pyplot as plt

        fig, ax = plt.subplots()
        xs = [p.price for p in data]
        ys = [p.pnl for p in data]
        ax.plot(xs, ys, label=quote.strategy.replace("_", " ").title())
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xlabel("BTC price ($)")
        ax.set_ylabel("Profit / Loss ($)")
        ax.set_title("Hedge payoff profile")
        ax.legend()
        fig.tight_layout()
        plt.savefig(target)
        plt.close(fig)

    # ------------------------------------------------------------------
    # Trade execution
    # ------------------------------------------------------------------
    async def execute_trade(
        self,
        user: str,
        quote: HedgeQuote,
        *,
        confirm: bool,
        external_id: str | None = None,
    ) -> dict[str, object]:
        self._ensure_enabled(quote.strategy)
        self._require_consent(user)
        if not confirm:
            raise ValueError("explicit confirmation required for hedging trades")
        if self.settings.max_notional is not None and quote.cost > self.settings.max_notional:
            raise ValueError("trade exceeds configured notional limit")
        mode = "paper" if self.settings.paper_trading else "live"
        txid = None
        if not self.settings.paper_trading:
            if not self.settings.trade_api_url:
                raise RuntimeError("trade_api_url must be configured for live trades")
            txid = await self._execute_remote_trade(quote)
        details = quote.to_dict()
        details.update({"mode": mode, "external_id": external_id, "txid": txid})
        self._log(user, "execute_trade", details)
        return {"mode": mode, "txid": txid, "cost": quote.cost}

    def audit_log(self, limit: int = 20) -> list[dict[str, object]]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT user, action, details, created_at FROM hedging_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        results: list[dict[str, object]] = []
        for user, action, details, created_at in rows:
            try:
                parsed = json.loads(details)
            except json.JSONDecodeError:
                parsed = {"raw": details}
            results.append(
                {
                    "user": user,
                    "action": action,
                    "details": parsed,
                    "created_at": created_at,
                }
            )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_enabled(self, strategy: str) -> None:
        if not self.settings.enabled:
            raise HedgingDisabledError("hedging module disabled")
        if strategy not in self.settings.allowed_strategies:
            raise HedgingDisabledError(f"strategy {strategy} not permitted")

    def _require_consent(self, user: str) -> None:
        if not self.settings.require_consent:
            return
        if not self.has_consent(user):
            raise HedgingConsentError("user has not completed hedging consent workflow")

    def _validate_answers(self, answers: dict[str, str]) -> None:
        if not self.settings.require_consent:
            return
        questions = self.settings.knowledge_check
        if not questions:
            raise ValueError("knowledge check not configured")
        normalized = {str(k): str(v).strip().lower() for k, v in answers.items()}
        for question in questions:
            try:
                response = normalized[question.id]
            except KeyError as exc:
                raise ValueError(f"missing answer for {question.id}") from exc
            valid = any(response == ans.strip().lower() for ans in question.correct_answers)
            if not valid:
                raise ValueError(f"incorrect answer for {question.id}")

    def _calculate_pnl(self, quote: HedgeQuote, price: float) -> float:
        if quote.strategy == "put_option":
            payoff = max(quote.strike - price, 0) * quote.size if quote.strike is not None else 0.0
            return payoff - quote.cost
        if quote.strategy == "short_futures":
            entry = quote.entry_price or quote.underlying
            return (entry - price) * quote.size
        raise ValueError(f"unknown strategy {quote.strategy}")

    def _resolve_price_range(
        self,
        quote: HedgeQuote,
        price_range: Sequence[float] | None,
    ) -> tuple[float, float]:
        if price_range is not None:
            if len(price_range) != 2:
                raise ValueError("price_range must contain exactly two values")
            start, end = float(price_range[0]), float(price_range[1])
        else:
            base = quote.underlying or quote.entry_price or 0.0
            start = base * 0.5 if base > 0 else 5000.0
            end = base * 1.5 if base > 0 else start * 2
        if start <= 0 or end <= 0 or end <= start:
            raise ValueError("invalid price range")
        return start, end

    async def _fetch_option_quote(self, **params: float) -> dict[str, float] | None:
        url = self.settings.option_quote_url
        if not url:
            return None
        formatted = url.format(**{k: params[k] for k in params})
        return await self._fetch_json(formatted)

    async def _fetch_futures_quote(self, **params: float) -> dict[str, float] | None:
        url = self.settings.futures_quote_url
        if not url:
            return None
        formatted = url.format(**{k: params[k] for k in params})
        return await self._fetch_json(formatted)

    async def _execute_remote_trade(self, quote: HedgeQuote) -> str:
        if not self.settings.trade_api_url:
            raise RuntimeError("trade_api_url not configured")
        payload = quote.to_dict()
        response = await self._fetch_json(self.settings.trade_api_url, data=payload)
        txid = response.get("txid") if isinstance(response, dict) else None
        if not txid:
            raise RuntimeError("trade execution failed")
        return str(txid)

    async def _fetch_json(self, url: str, *, data: dict | None = None) -> dict[str, float] | None:
        import urllib.request

        def _request() -> dict[str, float] | None:
            req = urllib.request.Request(url)
            if data is not None:
                payload = json.dumps(data).encode("utf-8")
                req.data = payload
                req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=5) as resp:  # pragma: no cover - IO
                body = resp.read()
                if not body:
                    return None
                return json.loads(body.decode("utf-8"))

        try:
            return await asyncio.to_thread(_request)
        except Exception:
            return None

    def _black_scholes_put(
        self,
        spot: float,
        strike: float,
        rate: float,
        vol: float,
        time: float,
    ) -> float:
        if spot <= 0 or strike <= 0:
            raise ValueError("spot and strike must be positive")
        if time <= 0:
            return max(strike - spot, 0.0)
        if vol <= 0:
            return max(strike - spot * math.exp(-rate * time), 0.0)
        sqrt_t = math.sqrt(time)
        d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * time) / (vol * sqrt_t)
        d2 = d1 - vol * sqrt_t

        return strike * math.exp(-rate * time) * self._norm_cdf(-d2) - spot * self._norm_cdf(-d1)

    def _norm_cdf(self, x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def _log(self, user: str, action: str, details: dict[str, object]) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO hedging_audit(user, action, details) VALUES (?,?,?)",
            (user, action, json.dumps(details, default=str)),
        )
        self.conn.commit()


__all__ = [
    "HedgingService",
    "HedgingDisabledError",
    "HedgingConsentError",
    "HedgeQuote",
    "PayoffPoint",
]
