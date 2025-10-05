"""Utilities for generating a consolidated loan health dashboard."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
import math

from ..config import Config
from ..db import get_connection
from .ltv import LoanState, compute_ltv, price_drop_to_reach_ltv
from .pricing import PriceService


def _classify_status(ltv: float, thresholds: Dict[str, float]) -> str:
    if math.isinf(ltv):
        return "liquidation_risk"
    if ltv >= thresholds["liquidation"]:
        return "liquidation_risk"
    if ltv >= thresholds["margin_call"]:
        return "margin_call"
    if ltv >= thresholds["warning"]:
        return "warning"
    return "safe"


def _recommended_action(status: str) -> str:
    if status == "liquidation_risk":
        return "Immediate action required: add collateral or repay to avoid liquidation."
    if status == "margin_call":
        return "Margin-call threshold hit. Transfer collateral or repay now."
    if status == "warning":
        return "Warning level reached. Consider adding collateral buffer."
    return "Position is within safe bands. Continue monitoring."


@dataclass
class DashboardSnapshot:
    profile_id: str
    generated_at: datetime
    btc_price: float
    ltv: float
    debt: float
    collateral_value: float
    collateral_breakdown: Dict[str, Dict[str, float]]
    thresholds: Dict[str, float]
    terms: Dict[str, Any]
    status: str
    recommended_action: str
    margin_buffer_pct: float
    margin_buffer_drop_pct: Optional[float]
    margin_price: Optional[float]
    last_loan_update: Optional[str]
    last_collateral_update: Optional[str]
    notes: list[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "generated_at": self.generated_at.isoformat(),
            "btc_price": self.btc_price,
            "ltv": self.ltv,
            "debt": self.debt,
            "collateral_value": self.collateral_value,
            "collateral_breakdown": self.collateral_breakdown,
            "thresholds": self.thresholds,
            "terms": self.terms,
            "status": self.status,
            "recommended_action": self.recommended_action,
            "margin_buffer_pct": self.margin_buffer_pct,
            "margin_buffer_drop_pct": self.margin_buffer_drop_pct,
            "margin_price": self.margin_price,
            "last_loan_update": self.last_loan_update,
            "last_collateral_update": self.last_collateral_update,
            "notes": list(self.notes),
        }


async def gather_dashboard_snapshot(
    config: Config,
    *,
    profile_id: str | None = None,
    price_service: PriceService | None = None,
    conn=None,
) -> DashboardSnapshot:
    """Collect a snapshot of loan health and platform terms."""

    profile = config.get_profile(profile_id)
    conn = conn or get_connection()
    price_service = price_service or PriceService()
    cur = conn.cursor()

    notes: list[str] = []

    def _load_cached_price() -> Optional[Tuple[float, Optional[str], str]]:
        cur.execute(
            "SELECT btc_price, created_at FROM ltv_history WHERE profile=? ORDER BY created_at DESC LIMIT 1",
            (profile.id,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return float(row[0]), row[1], "ltv history"
        cur.execute(
            "SELECT btc_price, created_at FROM collateral_snapshot WHERE profile=? ORDER BY id DESC LIMIT 1",
            (profile.id,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return float(row[0]), row[1], "collateral snapshot"
        return None

    try:
        btc_price = await price_service.get_price()
    except Exception as exc:
        cached = _load_cached_price()
        if cached is None:
            raise RuntimeError("all price sources failed and no cached price available") from exc
        btc_price, cached_at, cached_source = cached
        if cached_at:
            notes.append(
                "Using cached BTC price from "
                f"{cached_source} recorded at {cached_at} because live price sources were unavailable."
            )
        else:
            notes.append(
                "Using cached BTC price from "
                f"{cached_source} because live price sources were unavailable."
            )
    cur.execute(
        "SELECT principal, interest, updated_at FROM loan WHERE profile = ?",
        (profile.id,),
    )
    loan_row = cur.fetchone()
    if loan_row:
        principal, interest, loan_updated = loan_row
    else:
        principal = float(profile.loan.get("principal", 0.0) or 0.0)
        interest = float(profile.loan.get("interest", 0.0) or 0.0)
        loan_updated = None

    cur.execute(
        "SELECT SUM(btc_collateral), SUM(usdt_collateral) FROM exchange_positions WHERE profile=?",
        (profile.id,),
    )
    exchange_totals = cur.fetchone()
    cur.execute(
        "SELECT name, platform, btc_collateral, usdt_collateral, loan_outstanding FROM exchange_positions WHERE profile=?",
        (profile.id,),
    )
    exchange_rows = cur.fetchall()
    if exchange_totals and exchange_totals[0] is not None:
        btc_amount = float(exchange_totals[0] or 0.0)
        usdt_amount = float(exchange_totals[1] or 0.0)
        collateral_updated = None
    else:
        cur.execute(
            "SELECT btc_amount, usdt_amount, btc_price, created_at FROM collateral_snapshot WHERE profile=? "
            "ORDER BY id DESC LIMIT 1",
            (profile.id,),
        )
        collateral_row = cur.fetchone()
        if collateral_row:
            btc_amount, usdt_amount, _, collateral_updated = collateral_row
        else:
            btc_amount = float(profile.collateral.get("btc", 0.0) or 0.0)
            usdt_amount = float(profile.collateral.get("usdt", 0.0) or 0.0)
            collateral_updated = None

    state = LoanState(
        float(principal or 0.0),
        float(interest or 0.0),
        float(btc_amount or 0.0),
        float(usdt_amount or 0.0),
        float(btc_price),
    )
    ltv = compute_ltv(state)
    debt = state.principal + state.interest
    collateral_value = state.btc_amount * state.btc_price + state.usdt_amount

    thresholds = {
        "warning": profile.thresholds.warning,
        "margin_call": profile.thresholds.margin_call,
        "liquidation": profile.thresholds.liquidation,
    }

    status = _classify_status(ltv, thresholds)
    recommended_action = _recommended_action(status)

    margin_buffer_pct = 0.0
    if not math.isinf(ltv):
        margin_buffer_pct = max(thresholds["margin_call"] - ltv, 0.0) * 100

    drop = price_drop_to_reach_ltv(state, thresholds["margin_call"])
    margin_buffer_drop_pct: Optional[float]
    margin_price: Optional[float]

    if math.isinf(drop):
        margin_buffer_drop_pct = None
        margin_price = None
        if state.btc_amount == 0:
            notes.append("No BTC collateral pledged; price declines do not increase LTV.")
        elif collateral_value == 0:
            notes.append("No collateral recorded. Add collateral immediately.")
        else:
            notes.append("Margin call not reachable before BTC reaches zero.")
    else:
        margin_buffer_drop_pct = max(drop, 0.0) * 100
        margin_price = state.btc_price * (1 - max(drop, 0.0))
        if drop == 0.0 and ltv >= thresholds["margin_call"]:
            notes.append("Margin-call buffer exhausted; take action now.")

    terms_dict = asdict(config.terms)

    collateral_breakdown = {
        "btc": {
            "amount": state.btc_amount,
            "usd_value": state.btc_amount * state.btc_price,
        },
        "usdt": {
            "amount": state.usdt_amount,
            "usd_value": state.usdt_amount,
        },
    }
    if exchange_rows:
        collateral_breakdown["exchanges"] = {
            name: {
                "platform": platform,
                "btc": btc,
                "usdt": usdt,
                "loan": loan,
            }
            for name, platform, btc, usdt, loan in exchange_rows
        }

    return DashboardSnapshot(
        profile_id=profile.id,
        generated_at=datetime.now(timezone.utc),
        btc_price=btc_price,
        ltv=ltv,
        debt=debt,
        collateral_value=collateral_value,
        collateral_breakdown=collateral_breakdown,
        thresholds=thresholds,
        terms=terms_dict,
        status=status,
        recommended_action=recommended_action,
        margin_buffer_pct=margin_buffer_pct,
        margin_buffer_drop_pct=margin_buffer_drop_pct,
        margin_price=margin_price,
        last_loan_update=loan_updated,
        last_collateral_update=collateral_updated,
        notes=notes,
    )


def render_dashboard(snapshot: DashboardSnapshot) -> str:
    """Return a human-readable dashboard summary."""

    def _format_pct(value: float) -> str:
        return f"{value * 100:.2f}%"

    lines = [
        f"Profile: {snapshot.profile_id}",
        f"Generated at: {snapshot.generated_at.isoformat()}",
        f"Status: {snapshot.status.replace('_', ' ').title()}",
    ]

    if math.isinf(snapshot.ltv):
        lines.append("LTV: undefined (no collateral recorded)")
    else:
        lines.append(f"LTV: {snapshot.ltv:.2%}")

    lines.extend(
        [
            f"BTC price: ${snapshot.btc_price:,.2f}",
            f"Debt outstanding: ${snapshot.debt:,.2f}",
            "Collateral:",
            "  BTC: "
            f"{snapshot.collateral_breakdown['btc']['amount']:.6f} ≈ ${snapshot.collateral_breakdown['btc']['usd_value']:,.2f}",
            "  USDT: "
            f"${snapshot.collateral_breakdown['usdt']['usd_value']:,.2f}",
            f"Total collateral value: ${snapshot.collateral_value:,.2f}",
            "Thresholds: "
            f"warning {_format_pct(snapshot.thresholds['warning'])}, "
            f"margin call {_format_pct(snapshot.thresholds['margin_call'])}, "
            f"liquidation {_format_pct(snapshot.thresholds['liquidation'])}",
            f"Recommended action: {snapshot.recommended_action}",
        ]
    )

    if snapshot.margin_buffer_pct > 0:
        if snapshot.margin_buffer_drop_pct is not None:
            lines.append(
                "Margin-call buffer: "
                f"{snapshot.margin_buffer_pct:.2f}% headroom (~{snapshot.margin_buffer_drop_pct:.2f}% price drop to ${snapshot.margin_price:,.2f})"
            )
        else:
            lines.append(
                "Margin-call buffer: unlimited (BTC price drops do not increase LTV)"
            )
    else:
        lines.append("Margin-call buffer: none")

    if snapshot.last_loan_update:
        lines.append(f"Last loan update: {snapshot.last_loan_update}")
    if snapshot.last_collateral_update:
        lines.append(f"Last collateral update: {snapshot.last_collateral_update}")

    lines.append("Platform terms:")
    for key, value in snapshot.terms.items():
        if value in (None, ""):
            continue
        label = key.replace("_", " ").title()
        if isinstance(value, float):
            if "fee" in key or key == "liquidation_fee":
                lines.append(f"  {label}: {value:.2%}")
            elif key in {"initial", "warning", "margin_call", "liquidation"}:
                lines.append(f"  {label}: {value:.2%}")
            else:
                lines.append(f"  {label}: {value}")
        else:
            lines.append(f"  {label}: {value}")

    if snapshot.notes:
        lines.append("Notes:")
        for note in snapshot.notes:
            lines.append(f"- {note}")

    return "\n".join(lines)


__all__ = ["DashboardSnapshot", "gather_dashboard_snapshot", "render_dashboard"]
