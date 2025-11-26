# Usage Guide

This guide walks through typical workflows supported by `loan_monitor`, highlighting which CLI commands to run and what they
do. All commands can be executed via `python -m loan_monitor.cli <command>` or by installing an entrypoint that forwards to the
same module.

## Prerequisites

1. Install dependencies (`pip install -r requirements.txt`).
2. Configure `config.yaml` with your loan, collateral, thresholds, reserves, notification preferences, and the `security` block (user roles + TOTP secrets).
3. (Optional) export `LOAN_MONITOR_CONFIG` to point to an environment-specific configuration file.
4. Generate a JWT for write commands via `python -m loan_monitor.cli auth --user <name> --totp <code>` and either pass it with `--token` or export `LOAN_MONITOR_TOKEN`.

## Monitoring LTV

The monitoring loop is orchestrated by `loan_monitor.services.monitor.LTVMonitor`. A simple runner might look like this:

```python
import asyncio

from loan_monitor.config import load_config
from loan_monitor.logging_setup import setup_logging
from loan_monitor.services.monitor import LTVMonitor

async def main():
    setup_logging()
    cfg = load_config()
    monitor = LTVMonitor(cfg)
    await monitor.run_forever()

if __name__ == "__main__":
    asyncio.run(main())
```

`LTVMonitor` will:

- Poll BTC/USDT prices asynchronously with caching and failover.
- Pull the latest loan principal and collateral snapshot from SQLite.
- Compare the current LTV against the configured thresholds.
- Emit alerts via the configured notifiers and execute reserve policies when thresholds are breached.

## Authentication

Reserve transfers and repayments require a valid JWT. Generate one from a configured user by supplying a fresh TOTP code:

```bash
python -m loan_monitor.cli auth --user trader --totp 123456
export LOAN_MONITOR_TOKEN="<token>"
```

Tokens expire after `token_ttl_seconds`; rerun the `auth` command whenever you receive an authentication error.

## Managing Reserves

Inspect pledged/unpledged balances:

```bash
python -m loan_monitor.cli show
```

Transfer collateral between reserves and pledged collateral:

```bash
python -m loan_monitor.cli transfer btc 0.05 to_collateral --token "$LOAN_MONITOR_TOKEN"
python -m loan_monitor.cli transfer usdt 100 to_reserve --token "$LOAN_MONITOR_TOKEN"
```

Transfers automatically update the SQLite tables so future alerts and dashboards reflect the new state.

## Repaying the Loan

Execute a repayment (with validation against overpayment):

```bash
python -m loan_monitor.cli repay 250 --token "$LOAN_MONITOR_TOKEN"
```

Simulate the impact without touching balances:

```bash
python -m loan_monitor.cli repay 250 --dry-run --token "$LOAN_MONITOR_TOKEN"
```

Both commands output the resulting principal and recalculated LTV. The repayment service deducts stablecoins from the `reserves`
 table when the action is not a dry run.

## Collateral Planning

Size collateral for a target starting LTV:

```bash
python -m loan_monitor.cli plan --btc-price 30000 --loan 10000 --target-ltv 0.5 --stablecoin-pct 0.3
```

The planner returns a JSON payload summarising BTC/USDT amounts, resulting LTV, and the BTC price drop required to reach the
margin-call threshold.

## Volatility Simulation

Model how different collateral mixes respond to BTC drawdowns:

```bash
python -m loan_monitor.cli simulate --btc-price 30000 --loan 10000 --drops 0.0 0.1 0.2 0.3 --stablecoin-pcts 0.0 0.2 0.4
```

Passing `--plot-file chart.png` will save a matplotlib plot comparing LTV trajectories per mix. Without a plot the results are
printed to stdout in a structured format.

## Stress Testing

Project extreme moves and compute remediation actions:

```bash
python -m loan_monitor.cli stress --btc-price 30000 --btc-amount 0.5 --usdt-amount 5000 --loan 10000 --drops 0.1 0.2 0.4 --safe-ltv 0.7
```

Each row reports:

- The simulated price drop and resulting BTC price.
- New LTV classification (safe, warning, margin_call, liquidation_risk).
- Additional collateral (USD) or repayment required to return to the target safe LTV.

## Dashboard Snapshot

Aggregate real-time metrics and platform terms:

```bash
python -m loan_monitor.cli dashboard
```

Use `--json` when integrating with monitoring dashboards or chatops bots:

```bash
python -m loan_monitor.cli dashboard --json | jq .
```

The dashboard highlights status, recommended actions, margin-call headroom, last update timestamps, and the terms pulled from the
`terms` block in configuration.

## Automation Tips

- Schedule `dashboard --json` to publish into a metrics store or status page.
- Feed the stress-test output into your alerting system to pre-compute how much collateral is needed for different scenarios.
- Combine `ReserveManager` transfers with exchange APIs to automate hot-wallet to collateral-account movements once approval
  workflows are in place.

## Next Steps

With the foundational workflows automated, the next development milestones include:

- Wiring the monitoring loop into a long-running service (systemd, Docker, Kubernetes CronJob).
- Integrating the JWT/TOTP flow with your identity provider or adding audit logging for security-sensitive actions.
- Expanding Prometheus coverage to include notifier latency, policy execution counts, and downstream API timings.
- Publishing these docs via MkDocs for wider organisational distribution.
