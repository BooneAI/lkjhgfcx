from __future__ import annotations

import argparse
import asyncio
import json
import math
import os

from .config import load_config
from .security import AuthManager
from .services.reserve import ReserveManager
from .services.repayment import RepaymentService
from .services.simulations import plan_collateral, simulate_volatility, stress_test
from .services.dashboard import gather_dashboard_snapshot, render_dashboard


def main() -> None:  # pragma: no cover - simple wrapper
    parser = argparse.ArgumentParser(prog="loan-monitor")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("show")
    auth_parser = sub.add_parser("auth")
    auth_parser.add_argument("--user", required=True)
    auth_parser.add_argument("--totp", required=True)

    t = sub.add_parser("transfer")
    t.add_argument("asset")
    t.add_argument("amount", type=float)
    t.add_argument("direction", choices=["to_collateral", "to_reserve"])
    t.add_argument("--token")
    r = sub.add_parser("repay")
    r.add_argument("amount", type=float)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--token")
    plan = sub.add_parser("plan")
    plan.add_argument("--loan", type=float)
    plan.add_argument("--btc-price", type=float, required=True)
    plan.add_argument("--target-ltv", type=float, default=0.5)
    plan.add_argument("--stablecoin-pct", type=float, default=0.2)
    plan.add_argument("--margin-threshold", type=float)
    sim = sub.add_parser("simulate")
    sim.add_argument("--loan", type=float)
    sim.add_argument("--btc-price", type=float, required=True)
    sim.add_argument("--total-collateral", type=float)
    sim.add_argument("--stablecoin-pcts", type=float, nargs="+", default=[0.0, 0.2])
    sim.add_argument("--drops", type=float, nargs="+", required=True)
    sim.add_argument("--plot-file")
    stress_parser = sub.add_parser("stress")
    stress_parser.add_argument("--loan", type=float)
    stress_parser.add_argument("--btc-price", type=float, required=True)
    stress_parser.add_argument("--btc-amount", type=float)
    stress_parser.add_argument("--usdt-amount", type=float)
    stress_parser.add_argument("--drops", type=float, nargs="+", required=True)
    stress_parser.add_argument("--safe-ltv", type=float, default=0.7)
    stress_parser.add_argument("--warning-threshold", type=float)
    stress_parser.add_argument("--margin-threshold", type=float)
    stress_parser.add_argument("--liquidation-threshold", type=float)
    dash_parser = sub.add_parser("dashboard")
    dash_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output",
    )
    args = parser.parse_args()
    cfg = load_config()
    auth = AuthManager(cfg.security)
    manager = ReserveManager(cfg)
    if args.cmd == "show":
        for asset, bal in manager.get_balances().items():
            print(f"{asset}: pledged={bal['pledged']} unpledged={bal['unpledged']}")
    elif args.cmd == "auth":
        token = auth.authenticate(args.user, args.totp)
        print(token)
    elif args.cmd == "transfer":
        _verify_token(auth, args.token, "trader")
        manager.transfer(args.asset, args.amount, to_collateral=args.direction == "to_collateral")
        bal = manager.get_balances()[args.asset]
        print(f"{args.asset}: pledged={bal['pledged']} unpledged={bal['unpledged']}")
    elif args.cmd == "repay":
        _verify_token(auth, args.token, "trader")
        service = RepaymentService(cfg)
        result = service.repay(args.amount, dry_run=args.dry_run)
        print(
            f"principal={result['principal']:.2f} ltv={result['ltv']:.2%}" +
            (f" txid={result['txid']}" if result['txid'] else "")
        )
    elif args.cmd == "plan":
        loan = args.loan if args.loan is not None else cfg.loan.get("principal")
        if loan is None:
            raise SystemExit("loan amount required via --loan or config")
        margin_threshold = (
            args.margin_threshold if args.margin_threshold is not None else cfg.thresholds.margin_call
        )
        result = plan_collateral(
            loan,
            args.btc_price,
            args.target_ltv,
            stablecoin_pct=args.stablecoin_pct,
            margin_threshold=margin_threshold,
        )
        buffer = result["margin_buffer"]
        buffer_text = "infinite" if math.isinf(buffer) else f"{buffer * 100:.2f}%"
        print(json.dumps(result, default=lambda o: o.__dict__, indent=2))
        print(f"Margin buffer before 85% call: {buffer_text}")
    elif args.cmd == "simulate":
        loan = args.loan if args.loan is not None else cfg.loan.get("principal")
        if loan is None:
            raise SystemExit("loan amount required via --loan or config")
        total_collateral = args.total_collateral
        if total_collateral is None:
            total_collateral = (
                cfg.collateral.get("btc", 0.0) * args.btc_price + cfg.collateral.get("usdt", 0.0)
            )
        results = simulate_volatility(
            loan,
            args.btc_price,
            total_collateral,
            args.stablecoin_pcts,
            args.drops,
            plot_file=args.plot_file,
        )
        for item in results:
            label = f"{int(round(item['stablecoin_pct'] * 100))}% USDT"
            print(label)
            for point in item["points"]:
                drop_pct = point.drop * 100
                print(f"  drop={drop_pct:.1f}% -> ltv={point.ltv:.2%}")
        if args.plot_file:
            print(f"Plot saved to {args.plot_file}")
    elif args.cmd == "stress":
        loan = args.loan if args.loan is not None else cfg.loan.get("principal")
        if loan is None:
            raise SystemExit("loan amount required via --loan or config")
        btc_amount = args.btc_amount if args.btc_amount is not None else cfg.collateral.get("btc", 0.0)
        usdt_amount = (
            args.usdt_amount if args.usdt_amount is not None else cfg.collateral.get("usdt", 0.0)
        )
        warning = (
            args.warning_threshold
            if args.warning_threshold is not None
            else cfg.thresholds.warning
        )
        margin = (
            args.margin_threshold
            if args.margin_threshold is not None
            else cfg.thresholds.margin_call
        )
        liquidation = (
            args.liquidation_threshold
            if args.liquidation_threshold is not None
            else cfg.thresholds.liquidation
        )
        results = stress_test(
            loan,
            btc_amount,
            usdt_amount,
            args.btc_price,
            args.drops,
            safe_ltv=args.safe_ltv,
            warning_threshold=warning,
            margin_threshold=margin,
            liquidation_threshold=liquidation,
        )
        for row in results:
            drop_pct = row["drop"] * 100
            print(
                "drop={:.1f}% price=${:.2f} ltv={:.2%} state={} additional_collateral=${:.2f} "
                "repay=${:.2f}".format(
                    drop_pct,
                    row["btc_price"],
                    row["ltv"],
                    row["state"],
                    row["additional_collateral"],
                    row["repayment_needed"],
                )
            )
    elif args.cmd == "dashboard":
        snapshot = asyncio.run(gather_dashboard_snapshot(cfg))
        if args.json:
            print(json.dumps(snapshot.as_dict(), indent=2))
        else:
            print(render_dashboard(snapshot))


def _verify_token(auth: AuthManager, token_arg: str | None, role: str) -> None:
    token = token_arg or os.environ.get("LOAN_MONITOR_TOKEN")
    if not token:
        raise SystemExit("Authentication token required. Run `loan-monitor auth` first or set LOAN_MONITOR_TOKEN.")
    auth.verify_token(token, required_role=role)


if __name__ == "__main__":  # pragma: no cover
    main()
