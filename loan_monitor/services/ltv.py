from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass
class LoanState:
    principal: float
    interest: float
    btc_amount: float
    usdt_amount: float
    btc_price: float


def compute_ltv(state: LoanState) -> float:
    """Return loan-to-value ratio."""
    debt = state.principal + state.interest
    collateral_value = state.btc_amount * state.btc_price + state.usdt_amount
    if collateral_value == 0:
        return float("inf")
    return debt / collateral_value


def price_drop_to_reach_ltv(state: LoanState, target_ltv: float) -> float:
    """Return BTC price drop fraction needed to reach a given LTV.

    The result is expressed as a decimal fraction (e.g. ``0.25`` for a 25%
    decline). ``math.inf`` indicates that the threshold cannot be reached via a
    BTC price drop (for example, when no BTC collateral is pledged). A result of
    ``0.0`` means the target is already met or exceeded.
    """

    if target_ltv <= 0:
        raise ValueError("target_ltv must be positive")

    debt = state.principal + state.interest
    btc_value = state.btc_amount * state.btc_price
    collateral_value = btc_value + state.usdt_amount

    if collateral_value == 0:
        return math.inf

    current_ltv = debt / collateral_value
    if current_ltv >= target_ltv:
        return 0.0

    if btc_value == 0:
        return math.inf

    numerator = target_ltv * collateral_value - debt
    denominator = target_ltv * btc_value

    if denominator == 0:
        return math.inf

    drop = numerator / denominator
    if drop <= 0:
        return 0.0
    if drop >= 1:
        return math.inf
    return drop


__all__ = ["LoanState", "compute_ltv", "price_drop_to_reach_ltv"]
