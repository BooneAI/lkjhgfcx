# Loan Monitor

A Python toolkit for monitoring loan-to-value (LTV) exposure on leveraged crypto loans and automating corrective actions. The
project bundles asynchronous price polling, reserve orchestration, repayment utilities, simulation tooling, and a human-friendly
dashboard so teams can keep liquidation risk under control.

The repository still contains the original Gatsby starter site, but the active code lives in the `loan_monitor/` Python package
and the accompanying test suite under `tests/`.

## Features

- **Config-driven setup** – Centralise API keys, loan balances, collateral holdings, thresholds, and platform terms in
  `config.yaml` or via `LOAN_MONITOR_CONFIG`.
- **Persistent state** – SQLite tables retain loan principal, interest accrual, and historical collateral snapshots across
  restarts.
- **Asynchronous LTV monitoring** – Fetch BTC/USDT prices with failover, compute LTV, and raise alerts through pluggable
  notifiers when thresholds (80 %, 85 %, 91 %) are breached.
- **Reserve & policy management** – Track pledged versus unpledged assets, transfer reserves, and execute automated top-up or
  repayment policies when alerts fire.
- **Repayment workflows** – Validate paydowns, update balances, and recalculate LTV with optional dry-run simulations.
- **Simulation suite** – Plan collateral buffers, model volatility across collateral mixes, and stress-test extreme price moves
  with tabular and plotting support.
  - **Dashboard snapshot** – Aggregate real-time metrics and static platform terms into a concise CLI dashboard or JSON payload for
    downstream tooling.
  - **Structured logging** – JSON log helpers make it simple to forward operational events into log pipelines.
  - **Role-based security** – JWT tokens issued after TOTP verification guard reserve transfers and repayments with trader/admin roles.
  - **Observability hooks** – Optional Prometheus metrics expose price latency, LTV trends, and alert counters for dashboards.

## Getting Started

1. **Create a virtual environment** (Python 3.10+ recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Review configuration** – Copy `config.yaml` or `.env.example` for your environment and update loan balances, collateral,
   alert thresholds, notification channels, platform terms, and the `security` block with user roles and TOTP secrets. The
   configuration loader will create a SQLite database at `loan_monitor/loan_monitor.db` on first run.
4. **Generate an authentication token** – Use the configured TOTP secret with the CLI to obtain a JWT for state-changing
   operations:
   ```bash
   python -m loan_monitor.cli auth --user trader --totp 123456
   export LOAN_MONITOR_TOKEN="<token>"
   ```
   Tokens expire after `token_ttl_seconds`; rerun the command when prompted.
5. **Initialise the database** – The schema is created automatically when any command touches the database. To pre-create it you
   can run:
   ```bash
   python - <<'PY'
   from loan_monitor.db import get_connection
   get_connection().close()
   PY
   ```
6. **Run tests** to verify the environment:
   ```bash
   pytest
   ```

## Configuration Reference

`config.yaml` governs runtime behaviour:

```yaml
api_keys:
  binance: ""
  coingecko: ""
loan:
  principal: 1000.0
  interest: 0.0
collateral:
  btc: 0.5
  usdt: 200.0
reserves:
  btc: 0.1
  usdt: 50.0
thresholds:
  warning: 0.8
  margin_call: 0.85
  liquidation: 0.91
poll_interval: 600
notification_channels:
  - console
policy:
  type: auto_topup
  topup_percent: 0.5
  repay_percent: 0.25
terms:
  platform: "Binance Flexible Loan"
  initial: 0.78
  warning: 0.8
  margin_call: 0.85
  liquidation: 0.91
  liquidation_fee: 0.02
  last_reviewed: "2023-01-01"
  documentation_url: "https://www.binance.com/en/support/faq/1c9dddb774054983992b8977ae36577a"
  notes: "Thresholds reflect Binance defaults and should be revalidated."
security:
  jwt_secret: "replace-with-strong-secret"
  token_ttl_seconds: 900
  users:
    trader:
      role: trader
      totp_secret: "JBSWY3DPEHPK3PXP"
observability:
  enable_metrics: true
  metrics_port: 9000
```

- **thresholds** drive alerting for the monitoring loop and the dashboard status classification.
- **policy** controls reserve behaviour: `auto_topup`, `auto_repay`, or `manual`.
- **terms** captures official exchange terms surfaced in the dashboard for fast auditing.
- **security** defines JWT/TOTP parameters and user roles. Generate TOTP codes with authenticator apps and keep secrets private.
- **observability** toggles the Prometheus metrics server and sets the scrape port.
- Secrets can be sourced from environment variables if you prefer to keep API keys out of the repository.

## CLI Usage

The toolkit exposes a unified CLI via `python -m loan_monitor.cli` or by installing the package and running `loan-monitor`.

| Command | Purpose |
| --- | --- |
| `show` | Display pledged and unpledged balances tracked by the reserve manager. |
| `auth --user <name> --totp <code>` | Exchange a valid TOTP code for a short-lived JWT token. |
| `transfer <asset> <amount> to_collateral|to_reserve [--token]` | Move funds between reserve pools (requires trader token). |
| `repay <amount> [--dry-run] [--token]` | Validate and execute a repayment against the loan principal (requires trader token). |
| `plan --btc-price <price> [--loan <amount>] [--target-ltv <ratio>]` | Calculate BTC/USDT collateral required for a target starting LTV. |
| `simulate --btc-price <price> --drops <...>` | Model LTV paths for multiple collateral mixes across price drops. |
| `stress --btc-price <price> --drops <...>` | Stress-test extreme moves and compute remediation (top-up / repay) actions. |
| `dashboard [--json]` | Render a consolidated snapshot of LTV, collateral, platform terms, and recommended actions. |

Example dashboard output:

```
$ python -m loan_monitor.cli dashboard
Generated at: 2024-01-15T12:34:56.789012+00:00
Status: Safe
LTV: 66.67%
BTC price: $200.00
Debt outstanding: $100.00
Collateral:
  BTC: 0.500000 ≈ $100.00
  USDT: $50.00
Total collateral value: $150.00
Thresholds: warning 80.00%, margin call 85.00%, liquidation 91.00%
Recommended action: Position is within safe bands. Continue monitoring.
Margin-call buffer: 18.33% headroom (~32.35% price drop to $135.30)
Last loan update: 2024-01-15 12:20:00
Last collateral update: 2024-01-15 12:30:00
Platform terms:
  Platform: Binance Flexible Loan
  Initial: 78.00%
  Warning: 80.00%
  Margin Call: 85.00%
  Liquidation: 91.00%
  Liquidation Fee: 2.00%
  Last Reviewed: 2023-01-01
  Documentation Url: https://www.binance.com/en/support/faq/1c9dddb774054983992b8977ae36577a
  Notes: Thresholds reflect Binance defaults and should be revalidated.
```

Pass `--json` to integrate the snapshot with other systems.

## Observability & Deployment

- **Prometheus metrics** – Set `observability.enable_metrics: true` in `config.yaml` to expose counters and gauges on
  `metrics_port` (default `9000`). Scrape the `/metrics` endpoint with Prometheus or another collector to monitor price latency,
  alert counts, and recent LTV values.
- **CI/CD** – GitHub Actions (`.github/workflows/ci.yml`) runs linting, tests, and a Docker build on every push or pull
  request to `main`, `master`, or `work` branches.
- **Container image** – Build a runnable image locally with `docker build . -t loan-monitor:latest`. The default command emits a
  JSON dashboard snapshot; override `CMD` or provide environment variables to run alternative workflows inside the container.

## Documentation

Detailed usage notes and operational guidance live in the `docs/` directory:

- [`docs/architecture.md`](docs/architecture.md) – component responsibilities, data flow, and persistence model.
- [`docs/usage.md`](docs/usage.md) – end-to-end CLI workflows for monitoring, reserve management, repayment, simulations, and the dashboard.
- [`docs/troubleshooting.md`](docs/troubleshooting.md) – guidance for common setup and runtime issues.

These markdown files can be fed into MkDocs or another static site generator if you want to publish internal docs.

## Testing

Run the automated test suite before committing changes:

```bash
pytest
ruff check loan_monitor tests
```

Tests cover price fetching, LTV computations, reserve policies, repayment validation, simulation helpers, and the dashboard
snapshot builder.

## Troubleshooting & Tips

- **Network sandboxing:** Price fetching requires outbound HTTPS access. When running offline, monkeypatch `PriceService.get_price`
  in your scripts or rely on the test doubles provided in the suite.
- **Database location:** Override the database path with `LOAN_MONITOR_DB_PATH` if running multiple environments on the same host.
- **Structured logs:** Import `loan_monitor.logging_setup.setup_logging()` early in your application entrypoint to receive JSON logs.
- **Configuration overrides:** Set `LOAN_MONITOR_CONFIG=/path/to/config.yaml` to load environment-specific settings without editing
  the repo copy.
- **Historical analysis:** The `collateral_snapshot` table records every submission; query it directly or export to your analytics
  stack for trend analysis.

## Roadmap

Future milestones include the security and observability enhancements outlined in the development plan: JWT + TOTP enforcement,
role-based access control, Prometheus metrics, multi-exchange diversification, and containerised deployment workflows.

---

Need help or have suggestions? Open an issue or share feedback so we can continue hardening the loan monitoring workflow.
