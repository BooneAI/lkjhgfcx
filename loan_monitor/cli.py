from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import getpass
from dataclasses import asdict
from typing import Any

from pathlib import Path

from .config import load_config, resolve_config_path, resolve_secrets_path
from .exchanges.manager import ExchangeManager
from .security import AuthManager
from .services.reserve import ReserveManager
from .services.repayment import RepaymentService
from .services.simulations import plan_collateral, simulate_volatility, stress_test
from .services.dashboard import gather_dashboard_snapshot, render_dashboard
from .services.hedging import HedgingService, HedgingConsentError
from .services.reporting import ReportingService
from .secrets import SecretManager, SecretError, SecretPassphraseRequired


def main() -> None:  # pragma: no cover - simple wrapper
    parser = argparse.ArgumentParser(prog="loan-monitor")
    sub = parser.add_subparsers(dest="cmd")
    show_parser = sub.add_parser("show")
    show_parser.add_argument("--profile")
    auth_parser = sub.add_parser("auth")
    auth_parser.add_argument("--user", required=True)
    auth_parser.add_argument("--totp", required=True)

    t = sub.add_parser("transfer")
    t.add_argument("asset")
    t.add_argument("amount", type=float)
    t.add_argument("direction", choices=["to_collateral", "to_reserve"])
    t.add_argument("--token")
    t.add_argument("--profile")
    r = sub.add_parser("repay")
    r.add_argument("amount", type=float)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--token")
    r.add_argument("--profile")
    plan = sub.add_parser("plan")
    plan.add_argument("--loan", type=float)
    plan.add_argument("--btc-price", type=float, required=True)
    plan.add_argument("--target-ltv", type=float, default=0.5)
    plan.add_argument("--stablecoin-pct", type=float, default=0.2)
    plan.add_argument("--margin-threshold", type=float)
    plan.add_argument("--profile")
    sim = sub.add_parser("simulate")
    sim.add_argument("--loan", type=float)
    sim.add_argument("--btc-price", type=float, required=True)
    sim.add_argument("--total-collateral", type=float)
    sim.add_argument("--stablecoin-pcts", type=float, nargs="+", default=[0.0, 0.2])
    sim.add_argument("--drops", type=float, nargs="+", required=True)
    sim.add_argument("--plot-file")
    sim.add_argument("--profile")
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
    stress_parser.add_argument("--profile")
    dash_parser = sub.add_parser("dashboard")
    dash_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output",
    )
    dash_parser.add_argument("--profile")
    report_parser = sub.add_parser("report")
    report_sub = report_parser.add_subparsers(dest="report_cmd")
    report_ltv = report_sub.add_parser("ltv")
    report_ltv.add_argument("--profile")
    report_ltv.add_argument("--hours", type=float)
    report_ltv.add_argument("--limit", type=int)
    report_ltv.add_argument("--json", action="store_true")
    report_ltv.add_argument("--csv")
    exchange = sub.add_parser("exchange")
    exchange_sub = exchange.add_subparsers(dest="exchange_cmd")
    exchange_bal = exchange_sub.add_parser("balances")
    exchange_bal.add_argument("--json", action="store_true")
    exchange_bal.add_argument("--profile")
    exchange_add = exchange_sub.add_parser("add")
    exchange_add.add_argument("--name", required=True)
    exchange_add.add_argument("--asset", choices=["btc", "usdt"], required=True)
    exchange_add.add_argument("--amount", type=float, required=True)
    exchange_add.add_argument("--token")
    exchange_add.add_argument("--profile")
    exchange_repay = exchange_sub.add_parser("repay")
    exchange_repay.add_argument("--name", required=True)
    exchange_repay.add_argument("--amount", type=float, required=True)
    exchange_repay.add_argument("--token")
    exchange_repay.add_argument("--profile")
    exchange_withdraw = exchange_sub.add_parser("withdraw")
    exchange_withdraw.add_argument("--name", required=True)
    exchange_withdraw.add_argument("--asset", choices=["btc", "usdt"], required=True)
    exchange_withdraw.add_argument("--amount", type=float, required=True)
    exchange_withdraw.add_argument("--confirm", action="store_true")
    exchange_withdraw.add_argument("--token")
    exchange_withdraw.add_argument("--profile")
    hedge = sub.add_parser("hedge")
    hedge_sub = hedge.add_subparsers(dest="hedge_cmd")
    consent = hedge_sub.add_parser("consent")
    consent.add_argument("--user", required=True)
    consent.add_argument("--answers", required=True, help="JSON mapping question id to answer")
    consent.add_argument("--acknowledge", action="store_true")
    consent.add_argument("--token")
    quote = hedge_sub.add_parser("quote")
    quote.add_argument("--type", choices=["put", "futures"], required=True)
    quote.add_argument("--strike", type=float)
    quote.add_argument("--expiry-days", type=int, default=30)
    quote.add_argument("--size", type=float, required=True)
    quote.add_argument("--iv", type=float)
    quote.add_argument("--price", type=float)
    quote.add_argument("--token")
    quote.add_argument("--payoff-file")
    quote.add_argument("--price-range", type=float, nargs=2)
    quote.add_argument("--points", type=int, default=25)
    execute = hedge_sub.add_parser("execute")
    execute.add_argument("--type", choices=["put", "futures"], required=True)
    execute.add_argument("--strike", type=float)
    execute.add_argument("--expiry-days", type=int, default=30)
    execute.add_argument("--size", type=float, required=True)
    execute.add_argument("--iv", type=float)
    execute.add_argument("--price", type=float)
    execute.add_argument("--confirm", action="store_true")
    execute.add_argument("--token")
    audit = hedge_sub.add_parser("audit")
    audit.add_argument("--limit", type=int, default=10)
    audit.add_argument("--token")
    secrets_parser = sub.add_parser("secrets")
    secrets_parser.add_argument("--config", help="Path to base config file")
    secrets_parser.add_argument("--env", help="Environment for overrides")
    secrets_parser.add_argument("--secrets-path", help="Override secrets file path")
    secrets_parser.add_argument("--passphrase", help="Secrets passphrase (optional)")
    secrets_sub = secrets_parser.add_subparsers(dest="secrets_cmd")
    secrets_sub.add_parser("list")
    secrets_get = secrets_sub.add_parser("get")
    secrets_get.add_argument("key")
    secrets_get.add_argument("--json", action="store_true")
    secrets_set = secrets_sub.add_parser("set")
    secrets_set.add_argument("key")
    secrets_set.add_argument("value")
    secrets_del = secrets_sub.add_parser("delete")
    secrets_del.add_argument("key")
    secrets_init = secrets_sub.add_parser("init")
    secrets_init.add_argument("--overwrite", action="store_true")

    args = parser.parse_args()

    if args.cmd == "secrets":
        _handle_secrets(args)
        return

    cfg = load_config()
    auth = AuthManager(cfg.security)
    exchange_manager = ExchangeManager(cfg)
    hedging_service = HedgingService(cfg)
    if args.cmd == "show":
        reserve_manager = ReserveManager(cfg, profile_id=args.profile)
        for asset, bal in reserve_manager.get_balances().items():
            print(f"{asset}: pledged={bal['pledged']} unpledged={bal['unpledged']}")
    elif args.cmd == "auth":
        token = auth.authenticate(args.user, args.totp)
        print(token)
    elif args.cmd == "transfer":
        payload = _verify_token(auth, args.token, "trader")
        reserve_manager = ReserveManager(cfg, profile_id=args.profile)
        reserve_manager.transfer(
            args.asset,
            args.amount,
            to_collateral=args.direction == "to_collateral",
            user=payload.get("sub"),
        )
        bal = reserve_manager.get_balances()[args.asset]
        print(f"{args.asset}: pledged={bal['pledged']} unpledged={bal['unpledged']}")
    elif args.cmd == "repay":
        payload = _verify_token(auth, args.token, "trader")
        service = RepaymentService(cfg, profile_id=args.profile)
        result = service.repay(
            args.amount,
            dry_run=args.dry_run,
            user=payload.get("sub"),
        )
        print(
            f"principal={result['principal']:.2f} ltv={result['ltv']:.2%}" +
            (f" txid={result['txid']}" if result['txid'] else "")
        )
    elif args.cmd == "plan":
        profile = cfg.get_profile(args.profile)
        loan = args.loan if args.loan is not None else profile.loan.get("principal")
        if loan is None:
            raise SystemExit("loan amount required via --loan or config")
        margin_threshold = (
            args.margin_threshold if args.margin_threshold is not None else profile.thresholds.margin_call
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
        profile = cfg.get_profile(args.profile)
        loan = args.loan if args.loan is not None else profile.loan.get("principal")
        if loan is None:
            raise SystemExit("loan amount required via --loan or config")
        total_collateral = args.total_collateral
        if total_collateral is None:
            total_collateral = (
                profile.collateral.get("btc", 0.0) * args.btc_price + profile.collateral.get("usdt", 0.0)
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
        profile = cfg.get_profile(args.profile)
        loan = args.loan if args.loan is not None else profile.loan.get("principal")
        if loan is None:
            raise SystemExit("loan amount required via --loan or config")
        btc_amount = args.btc_amount if args.btc_amount is not None else profile.collateral.get("btc", 0.0)
        usdt_amount = (
            args.usdt_amount if args.usdt_amount is not None else profile.collateral.get("usdt", 0.0)
        )
        warning = (
            args.warning_threshold
            if args.warning_threshold is not None
            else profile.thresholds.warning
        )
        margin = (
            args.margin_threshold
            if args.margin_threshold is not None
            else profile.thresholds.margin_call
        )
        liquidation = (
            args.liquidation_threshold
            if args.liquidation_threshold is not None
            else profile.thresholds.liquidation
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
        snapshot = asyncio.run(gather_dashboard_snapshot(cfg, profile_id=args.profile))
        if args.json:
            print(json.dumps(snapshot.as_dict(), indent=2))
        else:
            print(render_dashboard(snapshot))
    elif args.cmd == "report":
        if args.report_cmd != "ltv":
            raise SystemExit("report subcommand required (use `loan-monitor report ltv`)")
        reporting = ReportingService(cfg)
        summary = reporting.summarize_ltv(
            profile_id=args.profile,
            limit=args.limit,
            hours=args.hours,
        )
        history = reporting.get_ltv_history(
            profile_id=args.profile,
            limit=args.limit,
            hours=args.hours,
        )
        if args.csv:
            path = reporting.export_history(
                Path(args.csv),
                profile_id=args.profile,
                limit=args.limit,
                hours=args.hours,
            )
            print(f"history exported to {path}")
        if args.json:
            payload = {
                "summary": summary,
                "history": [entry.to_dict() for entry in history],
            }
            print(json.dumps(payload, indent=2))
        else:
            print(f"LTV history summary ({summary.get('count', 0)} entries)")
            latest = summary.get("latest") or {}
            if latest.get("ltv") is not None:
                print(
                    "  Latest: {ltv:.2%} at {ts}".format(
                        ltv=latest["ltv"], ts=latest.get("created_at", "unknown")
                    )
                )
            if summary.get("change") is not None:
                print(f"  Change over window: {summary['change']:+.2%}")
            min_ltv = summary.get("min")
            max_ltv = summary.get("max")
            if min_ltv is not None and max_ltv is not None:
                print(f"  Range: {min_ltv:.2%} – {max_ltv:.2%}")
            print(
                "  Margin events: {} ({} liquidation)".format(
                    summary.get("margin_events", 0),
                    summary.get("liquidation_events", 0),
                )
            )
            recent = summary.get("recent_trend")
            if recent is not None:
                direction = "up" if recent > 0 else ("down" if recent < 0 else "flat")
                print(f"  Recent trend: {recent:+.2%} ({direction})")
    elif args.cmd == "hedge":
        if not cfg.hedging.enabled:
            raise SystemExit("Hedging module disabled in configuration")
        if args.hedge_cmd is None:
            raise SystemExit("Specify a hedging subcommand")
        if args.hedge_cmd == "consent":
            payload = _verify_token(auth, args.token, "trader")
            if payload.get("sub") != args.user:
                raise SystemExit("Token subject does not match --user")
            try:
                answers = json.loads(args.answers)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid answers payload: {exc}")
            hedging_service.record_consent(
                args.user,
                {str(k): str(v) for k, v in answers.items()},
                acknowledge=args.acknowledge,
            )
            print("Consent recorded")
        elif args.hedge_cmd == "quote":
            payload = _verify_token(auth, args.token, "trader")
            user = payload.get("sub")
            if not user:
                raise SystemExit("Token missing subject claim")
            try:
                if args.type == "put":
                    if args.strike is None:
                        raise SystemExit("--strike required for put quotes")
                    quote_obj = asyncio.run(
                        hedging_service.quote_put_option(
                            user,
                            strike=args.strike,
                            expiry_days=args.expiry_days,
                            size=args.size,
                            implied_vol=args.iv,
                            underlying=args.price,
                        )
                    )
                else:
                    quote_obj = asyncio.run(
                        hedging_service.quote_short_futures(
                            user,
                            size=args.size,
                            entry_price=args.price,
                        )
                    )
            except (HedgingConsentError, ValueError) as exc:
                raise SystemExit(str(exc))
            if args.payoff_file:
                hedging_service.plot_payoff(
                    quote_obj,
                    args.payoff_file,
                    price_range=args.price_range,
                    points=args.points,
                )
            payoff = hedging_service.payoff_profile(
                quote_obj,
                price_range=args.price_range,
                points=args.points,
            )
            payload = quote_obj.to_dict()
            payload["payoff"] = [p.__dict__ for p in payoff]
            print(json.dumps(payload, indent=2))
            if args.payoff_file:
                print(f"Payoff chart saved to {args.payoff_file}")
        elif args.hedge_cmd == "execute":
            required_role = "admin" if not cfg.hedging.paper_trading else "trader"
            payload = _verify_token(auth, args.token, required_role)
            user = payload.get("sub")
            if not user:
                raise SystemExit("Token missing subject claim")
            try:
                if args.type == "put":
                    if args.strike is None:
                        raise SystemExit("--strike required for put trades")
                    quote_obj = asyncio.run(
                        hedging_service.quote_put_option(
                            user,
                            strike=args.strike,
                            expiry_days=args.expiry_days,
                            size=args.size,
                            implied_vol=args.iv,
                            underlying=args.price,
                        )
                    )
                else:
                    quote_obj = asyncio.run(
                        hedging_service.quote_short_futures(
                            user,
                            size=args.size,
                            entry_price=args.price,
                        )
                    )
                result = asyncio.run(
                    hedging_service.execute_trade(
                        user,
                        quote_obj,
                        confirm=args.confirm,
                    )
                )
            except (HedgingConsentError, ValueError, RuntimeError) as exc:
                raise SystemExit(str(exc))
            print(json.dumps(result, indent=2))
        elif args.hedge_cmd == "audit":
            payload = _verify_token(auth, args.token, "admin")
            log = hedging_service.audit_log(limit=args.limit)
            print(json.dumps(log, indent=2))
    elif args.cmd == "exchange":
        if args.exchange_cmd is None:
            raise SystemExit("Specify an exchange subcommand")
        if args.exchange_cmd == "balances":
            balances = exchange_manager.list_balances(profile_id=args.profile)
            payload = {name: asdict(bal) for name, bal in balances.items()}
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                for name, bal in payload.items():
                    print(
                        f"{name} ({bal['btc_collateral']} BTC, ${bal['usdt_collateral']} USDT collateral, loan ${bal['loan_outstanding']})"
                    )
        elif args.exchange_cmd == "add":
            payload = _verify_token(auth, args.token, "trader")
            try:
                bal = exchange_manager.add_collateral(
                    args.profile,
                    args.name,
                    args.asset,
                    args.amount,
                    user=payload.get("sub"),
                )
            except ValueError as exc:
                raise SystemExit(str(exc))
            print(json.dumps(asdict(bal), indent=2))
        elif args.exchange_cmd == "repay":
            payload = _verify_token(auth, args.token, "trader")
            try:
                bal = exchange_manager.repay(
                    args.profile,
                    args.name,
                    args.amount,
                    user=payload.get("sub"),
                )
            except ValueError as exc:
                raise SystemExit(str(exc))
            print(json.dumps(asdict(bal), indent=2))
        elif args.exchange_cmd == "withdraw":
            payload = _verify_token(auth, args.token, "admin")
            try:
                result = exchange_manager.withdraw(
                    args.profile,
                    args.name,
                    args.asset,
                    args.amount,
                    user=payload.get("sub"),
                    confirm=args.confirm,
                )
            except ValueError as exc:
                raise SystemExit(str(exc))
            output = {
                "ltv": result["ltv"],
                "totals": result["totals"],
                "exchange_balances": asdict(result["exchange_balances"]),
            }
            print(json.dumps(output, indent=2))
        else:
            raise SystemExit("Unknown exchange subcommand")
    else:
        raise SystemExit("Unknown command")


def _handle_secrets(args: argparse.Namespace) -> None:
    env_name = args.env or os.environ.get("LOAN_MONITOR_ENV")
    config_override = None
    if getattr(args, "config", None):
        config_override = Path(args.config).expanduser().resolve()
    config_path = resolve_config_path(config_override)
    secrets_path = (
        Path(args.secrets_path).expanduser().resolve()
        if getattr(args, "secrets_path", None)
        else resolve_secrets_path(config_path, env_name)
    )
    passphrase = args.passphrase or os.environ.get("LOAN_MONITOR_SECRET_PASSPHRASE")
    manager = SecretManager(secrets_path, passphrase=passphrase)

    try:
        if args.secrets_cmd == "init":
            _ensure_passphrase(manager, confirm=True)
            manager.initialise(overwrite=args.overwrite)
            print(f"Initialised secrets store at {secrets_path}")
        elif args.secrets_cmd == "set":
            _ensure_passphrase(manager)
            manager.set(args.key, _parse_secret_value(args.value))
            print(f"Stored secret for {args.key}")
        elif args.secrets_cmd == "get":
            _ensure_passphrase(manager)
            try:
                value = manager.get(args.key)
            except KeyError:
                raise SystemExit(f"No secret stored for {args.key}")
            if args.json:
                print(json.dumps(value, indent=2))
            else:
                print(value)
        elif args.secrets_cmd == "delete":
            _ensure_passphrase(manager)
            try:
                manager.delete(args.key)
            except KeyError:
                raise SystemExit(f"No secret stored for {args.key}")
            print(f"Deleted secret {args.key}")
        elif args.secrets_cmd == "list":
            _ensure_passphrase(manager)
            keys = list(manager.list_keys())
            if not keys:
                print("<empty>")
            else:
                for key in keys:
                    print(key)
        else:
            raise SystemExit("Specify a secrets subcommand")
    except SecretPassphraseRequired:
        raise SystemExit(
            "Secrets passphrase required. Provide --passphrase, set "
            "LOAN_MONITOR_SECRET_PASSPHRASE, or respond to the prompt."
        )
    except SecretError as exc:
        raise SystemExit(str(exc))


def _ensure_passphrase(manager: SecretManager, *, confirm: bool = False) -> None:
    if manager.passphrase is None:
        manager.passphrase = _prompt_passphrase(confirm=confirm)


def _prompt_passphrase(*, confirm: bool = False) -> str:
    first = getpass.getpass("Secret passphrase: ")
    if confirm:
        second = getpass.getpass("Confirm passphrase: ")
        if first != second:
            raise SystemExit("Passphrases do not match")
    return first


def _parse_secret_value(raw: str) -> Any:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _verify_token(auth: AuthManager, token_arg: str | None, role: str) -> dict:
    token = token_arg or os.environ.get("LOAN_MONITOR_TOKEN")
    if not token:
        raise SystemExit("Authentication token required. Run `loan-monitor auth` first or set LOAN_MONITOR_TOKEN.")
    return auth.verify_token(token, required_role=role)


if __name__ == "__main__":  # pragma: no cover
    main()
