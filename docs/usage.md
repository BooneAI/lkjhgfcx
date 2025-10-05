# Usage Guide

This guide walks through typical workflows supported by `loan_monitor`, highlighting which CLI commands to run and what they
do. All commands can be executed via `python -m loan_monitor.cli <command>` or by installing an entrypoint that forwards to the
same module.

## Prerequisites

1. Install dependencies (`pip install -r requirements.txt`).
2. Configure `config.yaml` with your loan, collateral, thresholds, reserves, notification preferences, and the `security` block (user roles + TOTP secrets). Define one or more profiles under the `profiles` array and set `default_profile` to the profile ID you want to target when `--profile` is not supplied.
3. Use environment overlays when needed: `LOAN_MONITOR_ENV=production` will load `config.production.yaml` after the base file.
4. (Recommended) store API keys and credentials in the encrypted secrets vault via `loan-monitor secrets` (see below) and supply the passphrase at runtime with `LOAN_MONITOR_SECRET_PASSPHRASE`.
5. Generate a JWT for write commands via `python -m loan_monitor.cli auth --user <name> --totp <code>` and either pass it with `--token` or export `LOAN_MONITOR_TOKEN`.

## Managing Secrets

The encrypted secrets manager keeps API keys and exchange credentials out of plain-text configuration files.

```bash
# Initialise a secrets file (prompts for a passphrase)
python -m loan_monitor.cli secrets --env production init

# Store values (JSON is supported)
python -m loan_monitor.cli secrets --env production set api_keys.binance "my-api-key"
python -m loan_monitor.cli secrets --env production set exchanges.binance.api_secret "my-secret"

# Review stored keys (requires the same passphrase)
python -m loan_monitor.cli secrets --env production list
```

Provide the passphrase when running the monitor via the `--passphrase` flag or `LOAN_MONITOR_SECRET_PASSPHRASE`. The loader merges
secrets with the base and environment-specific configuration so the runtime sees a single combined config dictionary.

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
    monitor = LTVMonitor(cfg)  # defaults to cfg.default_profile
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
python -m loan_monitor.cli show --profile core
```

Omit `--profile` to fall back to the default profile configured in `config.yaml`.

Transfer collateral between reserves and pledged collateral:

```bash
python -m loan_monitor.cli transfer btc 0.05 to_collateral --profile core --token "$LOAN_MONITOR_TOKEN"
python -m loan_monitor.cli transfer usdt 100 to_reserve --profile core --token "$LOAN_MONITOR_TOKEN"
```

Transfers automatically update the SQLite tables so future alerts and dashboards reflect the new state.

## Managing Exchange Collateral

Define exchange connectors in `config.yaml` under the `exchanges` list. Each entry records the platform, initial collateral, any
outstanding loan balance, and a `safe_withdrawal_ltv` guardrail. The CLI exposes helper commands:

```bash
# Summarise balances per exchange
python -m loan_monitor.cli exchange balances --profile core

# Add collateral (requires trader token)
python -m loan_monitor.cli exchange add --name binance-main --asset btc --amount 0.05 --profile core --token "$LOAN_MONITOR_TOKEN"

# Repay an exchange-specific loan (requires trader token)
python -m loan_monitor.cli exchange repay --name binance-main --amount 100 --profile core --token "$LOAN_MONITOR_TOKEN"

# Withdraw collateral (requires admin token and explicit confirmation)
python -m loan_monitor.cli exchange withdraw --name binance-main --asset btc --amount 0.01 --confirm --profile core --token "$LOAN_MONITOR_TOKEN"
```

Withdrawals recompute portfolio LTV using aggregated balances across exchanges. The command aborts if the resulting LTV would
exceed the exchange's `safe_withdrawal_ltv` or the global margin-call threshold. Every exchange action is recorded in the
`audit_log` table.

## Repaying the Loan

Execute a repayment (with validation against overpayment):

```bash
python -m loan_monitor.cli repay 250 --profile core --token "$LOAN_MONITOR_TOKEN"
```

Simulate the impact without touching balances:

```bash
python -m loan_monitor.cli repay 250 --dry-run --profile core --token "$LOAN_MONITOR_TOKEN"
```

Both commands output the resulting principal and recalculated LTV. The repayment service deducts stablecoins from the `reserves`
 table when the action is not a dry run.

## Collateral Planning

Size collateral for a target starting LTV:

```bash
python -m loan_monitor.cli plan --btc-price 30000 --loan 10000 --target-ltv 0.5 --stablecoin-pct 0.3 --profile core
```

The planner returns a JSON payload summarising BTC/USDT amounts, resulting LTV, and the BTC price drop required to reach the
margin-call threshold.

## Volatility Simulation

Model how different collateral mixes respond to BTC drawdowns:

```bash
python -m loan_monitor.cli simulate --btc-price 30000 --loan 10000 --drops 0.0 0.1 0.2 0.3 --stablecoin-pcts 0.0 0.2 0.4 --profile core
```

Passing `--plot-file chart.png` will save a matplotlib plot comparing LTV trajectories per mix. Without a plot the results are
printed to stdout in a structured format.

## Stress Testing

Project extreme moves and compute remediation actions:

```bash
python -m loan_monitor.cli stress --btc-price 30000 --btc-amount 0.5 --usdt-amount 5000 --loan 10000 --drops 0.1 0.2 0.4 --safe-ltv 0.7 --profile core
```

## Historical Reporting

The monitor records every LTV calculation in the `ltv_history` table, enabling long-lived analytics:

```bash
python -m loan_monitor.cli report ltv --profile core --hours 24 --json
```

The command emits summary statistics (average, range, recent trend) and the raw data points. Add `--csv history.csv` to export a
time-series snapshot for audits or further analysis.

Each row reports:

- The simulated price drop and resulting BTC price.
- New LTV classification (safe, warning, margin_call, liquidation_risk).
- Additional collateral (USD) or repayment required to return to the target safe LTV.

## Dashboard Snapshot

Aggregate real-time metrics and platform terms:

```bash
python -m loan_monitor.cli dashboard --profile core
```

Use `--json` when integrating with monitoring dashboards or chatops bots:

```bash
python -m loan_monitor.cli dashboard --profile core --json | jq .
```

The dashboard highlights status, recommended actions, margin-call headroom, last update timestamps, and the terms pulled from the
`terms` block in configuration.

## Hedging Module (Opt-In)

Hedging functionality is disabled by default. Set `hedging.enabled: true` in configuration to expose the `hedge` CLI namespace.
Every hedging action requires user consent and a trader (or admin) token.

### 1. Record Consent

```bash
python -m loan_monitor.cli hedge consent --user trader \
    --answers '{"risk": "premium", "leverage": "yes"}' \
    --acknowledge --token "$LOAN_MONITOR_TOKEN"
```

The answers must match the configured knowledge-check prompts. Responses are stored in the `hedging_consent` table.

### 2. Request Quotes and Payoff Charts

```bash
python -m loan_monitor.cli hedge quote --type put --strike 25000 --expiry-days 30 \
    --size 0.5 --token "$LOAN_MONITOR_TOKEN" --payoff-file put.png
```

- `--type put` quotes a protective put. Provide `--strike`, `--expiry-days`, and `--size` (BTC quantity).
- `--type futures` quotes a short futures hedge. Provide `--size` (BTC notional) and optionally `--price` for a manual entry level.
- `--payoff-file` saves a Matplotlib payoff chart; omit it to just print JSON with payoff samples.

Quotes attempt to call configured option/futures endpoints and fall back to a Black–Scholes model (puts) or linear payoff (short
futures). Output includes premium, breakeven, max loss/profit, and payoff samples for automation.

### 3. Execute Trades with Confirmation

```bash
python -m loan_monitor.cli hedge execute --type futures --size 1.0 --confirm --token "$LOAN_MONITOR_TOKEN"
```

- `--confirm` is mandatory; omitting it causes the command to exit with an error.
- Live trades (`paper_trading: false`) require an admin token and a configured `trade_api_url`.

### 4. Review the Audit Log

```bash
python -m loan_monitor.cli hedge audit --limit 20 --token "$ADMIN_TOKEN"
```

Audit entries capture the user, action, and payload for consent, quotes, and executions. Use this feed to integrate with
compliance tooling or SIEM pipelines.

## FastAPI API & Web Dashboard

The FastAPI app in `loan_monitor.web` mirrors the CLI dashboard and exposes history, summary, and action endpoints:

- `POST /auth/token` – exchange a username + TOTP code for a JWT (roles are configured under `security.users`).
- `GET /api/profiles` – list profile identifiers and metadata for UI selectors.
- `GET /api/dashboard` – return the latest consolidated snapshot as JSON.
- `GET /api/history` / `GET /api/history/summary` – pull time-series entries and aggregate statistics.
- `POST /api/actions/reserves/transfer` – move reserves to or from pledged collateral (trader role required).
- `POST /api/actions/repay` – repay principal and receive the updated LTV (trader role required).

Start the API with uvicorn:

```bash
uvicorn loan_monitor.web:app --host 0.0.0.0 --port 8000
```

Run the Gatsby dashboard against the API (set the base URL if the port differs):

```bash
npm install   # first run only
GATSBY_API_BASE_URL="http://localhost:8000" npm run start
```

Visit `http://localhost:5000/dashboard` to sign in with a TOTP code, view LTV metrics, inspect the sparkline, toggle auto-refresh,
and trigger repayments or reserve transfers from the browser.

For health monitoring and alert wiring, see [Observability & Alerting](observability.md).

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
