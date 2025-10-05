"""Simulation and planning utilities for collateral management."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable, Sequence

from .ltv import LoanState, compute_ltv


@dataclass(slots=True)
class VolatilityPoint:
    """Single simulation point for a price drop scenario."""

    drop: float
    ltv: float


def _validate_percentage(name: str, value: float) -> None:
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


def plan_collateral(
    loan_amount: float,
    btc_price: float,
    target_ltv: float,
    *,
    stablecoin_pct: float = 0.20,
    margin_threshold: float = 0.85,
) -> dict[str, float | bool]:
    """Calculate required collateral mix and buffer until margin call.

    Returns a dictionary with BTC/USDT allocation, resulting LTV, and the
    percentage BTC price drop that would reach the margin-call threshold.
    """

    if loan_amount <= 0:
        raise ValueError("loan_amount must be positive")
    if btc_price <= 0:
        raise ValueError("btc_price must be positive")
    if not 0 < target_ltv < 1:
        raise ValueError("target_ltv must be between 0 and 1")
    if not 0 < margin_threshold < 1:
        raise ValueError("margin_threshold must be between 0 and 1")
    _validate_percentage("stablecoin_pct", stablecoin_pct)

    total_collateral = loan_amount / target_ltv
    usdt_amount = total_collateral * stablecoin_pct
    btc_value = total_collateral - usdt_amount
    btc_amount = btc_value / btc_price if btc_value > 0 else 0.0

    state = LoanState(
        principal=loan_amount,
        interest=0.0,
        btc_amount=btc_amount,
        usdt_amount=usdt_amount,
        btc_price=btc_price,
    )
    ltv = compute_ltv(state)

    if ltv >= margin_threshold:
        buffer = 0.0
    else:
        required_collateral_for_margin = loan_amount / margin_threshold
        if btc_value <= 0 or required_collateral_for_margin <= usdt_amount:
            buffer = math.inf
        else:
            needed_btc_value_after_drop = required_collateral_for_margin - usdt_amount
            ratio = needed_btc_value_after_drop / btc_value
            buffer = 1.0 - ratio
            buffer = max(0.0, min(buffer, 1.0))

    return {
        "loan_amount": loan_amount,
        "target_ltv": target_ltv,
        "ltv": ltv,
        "total_collateral": total_collateral,
        "btc_amount": btc_amount,
        "usdt_amount": usdt_amount,
        "margin_buffer": buffer,
        "buffer_is_safe": math.isinf(buffer) or buffer >= 0.15,
        "stablecoin_pct": stablecoin_pct,
    }


def simulate_volatility(
    loan_amount: float,
    btc_price: float,
    total_collateral: float,
    stablecoin_pcts: Sequence[float],
    drops: Sequence[float],
    *,
    plot_file: str | Path | None = None,
) -> list[dict[str, float | list[VolatilityPoint]]]:
    """Simulate LTV across price drops for different collateral mixes."""

    if loan_amount <= 0:
        raise ValueError("loan_amount must be positive")
    if btc_price <= 0:
        raise ValueError("btc_price must be positive")
    if total_collateral <= 0:
        raise ValueError("total_collateral must be positive")
    if not drops:
        raise ValueError("drops must not be empty")

    results: list[dict[str, float | list[VolatilityPoint]]] = []
    drop_values: list[float] = []
    for drop in drops:
        if not -0.99 <= drop <= 0.99:
            raise ValueError("drops must be between -0.99 and 0.99")
        drop_values.append(drop)

    for pct in stablecoin_pcts:
        _validate_percentage("stablecoin_pct", pct)
        usdt_amount = total_collateral * pct
        btc_value = total_collateral - usdt_amount
        btc_amount = btc_value / btc_price if btc_value > 0 else 0.0
        starting_state = LoanState(loan_amount, 0.0, btc_amount, usdt_amount, btc_price)
        points: list[VolatilityPoint] = []
        for drop in drop_values:
            new_price = btc_price * (1.0 - drop)
            state = LoanState(loan_amount, 0.0, btc_amount, usdt_amount, new_price)
            ltv = compute_ltv(state)
            points.append(VolatilityPoint(drop=drop, ltv=ltv))
        results.append(
            {
                "stablecoin_pct": pct,
                "btc_amount": btc_amount,
                "usdt_amount": usdt_amount,
                "starting_ltv": compute_ltv(starting_state),
                "points": points,
            }
        )

    if plot_file:
        import matplotlib

        matplotlib.use("Agg", force=True)
        from matplotlib import pyplot as plt

        fig, ax = plt.subplots()
        for item in results:
            points = item["points"]
            xs = [p.drop * 100 for p in points]
            ys = [p.ltv * 100 for p in points]
            label = f"{int(round(item['stablecoin_pct'] * 100))}% USDT"
            ax.plot(xs, ys, marker="o", label=label)
        ax.axhline(85, color="orange", linestyle="--", label="85% margin call")
        ax.axhline(91, color="red", linestyle=":", label="91% liquidation")
        ax.set_xlabel("BTC price drop (%)")
        ax.set_ylabel("LTV (%)")
        ax.set_title("LTV response to BTC price drops")
        ax.legend()
        fig.tight_layout()
        plt.savefig(plot_file)
        plt.close(fig)

    return results


def stress_test(
    loan_amount: float,
    btc_amount: float,
    usdt_amount: float,
    btc_price: float,
    drops: Iterable[float],
    *,
    safe_ltv: float = 0.70,
    warning_threshold: float = 0.80,
    margin_threshold: float = 0.85,
    liquidation_threshold: float = 0.91,
) -> list[dict[str, float]]:
    """Stress test loan under price drops and compute remediation actions."""

    if loan_amount <= 0:
        raise ValueError("loan_amount must be positive")
    if btc_price <= 0:
        raise ValueError("btc_price must be positive")
    if btc_amount < 0 or usdt_amount < 0:
        raise ValueError("asset amounts cannot be negative")
    if not 0 < safe_ltv < 1:
        raise ValueError("safe_ltv must be between 0 and 1")
    for threshold in (warning_threshold, margin_threshold, liquidation_threshold):
        if not 0 < threshold < 1:
            raise ValueError("thresholds must be between 0 and 1")

    results: list[dict[str, float]] = []
    drops_list = list(drops)
    if not drops_list:
        raise ValueError("drops must not be empty")

    debt = loan_amount
    for drop in drops_list:
        if not -0.99 <= drop <= 0.99:
            raise ValueError("drops must be between -0.99 and 0.99")
        new_price = btc_price * (1.0 - drop)
        state = LoanState(debt, 0.0, btc_amount, usdt_amount, new_price)
        ltv = compute_ltv(state)
        collateral_value = state.btc_amount * state.btc_price + state.usdt_amount

        if ltv >= liquidation_threshold:
            status = "liquidation_risk"
        elif ltv >= margin_threshold:
            status = "margin_call"
        elif ltv >= warning_threshold:
            status = "warning"
        else:
            status = "safe"

        target_collateral = debt / safe_ltv
        additional_collateral = max(0.0, target_collateral - collateral_value)
        repayment_needed = max(0.0, debt - safe_ltv * collateral_value)
        repayment_needed = min(repayment_needed, debt)

        results.append(
            {
                "drop": drop,
                "btc_price": new_price,
                "ltv": ltv,
                "state": status,
                "collateral_value": collateral_value,
                "additional_collateral": additional_collateral,
                "repayment_needed": repayment_needed,
            }
        )

    return results


__all__ = ["plan_collateral", "simulate_volatility", "stress_test", "VolatilityPoint"]
