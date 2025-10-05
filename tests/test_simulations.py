import math
from pathlib import Path

import pytest

from loan_monitor.services.simulations import (
    plan_collateral,
    simulate_volatility,
    stress_test,
)


def test_plan_collateral_buffer_and_allocations():
    result = plan_collateral(
        loan_amount=500.0,
        btc_price=25_000.0,
        target_ltv=0.5,
        stablecoin_pct=0.2,
        margin_threshold=0.85,
    )
    assert pytest.approx(result["total_collateral"], rel=1e-5) == 1000.0
    assert pytest.approx(result["btc_amount"], rel=1e-5) == 0.032
    assert pytest.approx(result["usdt_amount"], rel=1e-5) == 200.0
    assert pytest.approx(result["ltv"], rel=1e-6) == 0.5
    assert result["buffer_is_safe"] is True
    assert result["margin_buffer"] > 0.5


def test_plan_collateral_infinite_buffer_when_stablecoin_covers():
    result = plan_collateral(
        loan_amount=500.0,
        btc_price=25_000.0,
        target_ltv=0.5,
        stablecoin_pct=1.0,
        margin_threshold=0.85,
    )
    assert math.isinf(result["margin_buffer"])
    assert result["buffer_is_safe"] is True


def test_simulate_volatility_outputs_points(tmp_path: Path):
    plot_file = tmp_path / "volatility.png"
    results = simulate_volatility(
        loan_amount=500.0,
        btc_price=25_000.0,
        total_collateral=1_000.0,
        stablecoin_pcts=[0.0, 0.2],
        drops=[0.0, 0.5],
        plot_file=plot_file,
    )
    assert plot_file.exists()
    assert len(results) == 2
    zero_stable = results[0]
    half_drop = next(p for p in zero_stable["points"] if abs(p.drop - 0.5) < 1e-9)
    assert pytest.approx(half_drop.ltv, rel=1e-6) == 1.0
    mixed = results[1]
    mixed_half = next(p for p in mixed["points"] if abs(p.drop - 0.5) < 1e-9)
    assert mixed_half.ltv < half_drop.ltv


def test_stress_test_labels_and_remediation():
    results = stress_test(
        loan_amount=500.0,
        btc_amount=0.04,
        usdt_amount=0.0,
        btc_price=25_000.0,
        drops=[0.0, 0.5],
        safe_ltv=0.7,
        warning_threshold=0.8,
        margin_threshold=0.85,
        liquidation_threshold=0.91,
    )
    assert results[0]["state"] == "safe"
    risk = results[1]
    assert risk["state"] == "liquidation_risk"
    assert pytest.approx(risk["ltv"], rel=1e-6) == 1.0
    assert pytest.approx(risk["additional_collateral"], rel=1e-6) == 214.285714
    assert pytest.approx(risk["repayment_needed"], rel=1e-6) == 150.0
