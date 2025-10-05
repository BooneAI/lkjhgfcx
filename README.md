# Loan Monitor

A Python toolkit for monitoring loan-to-value (LTV) exposure on leveraged crypto loans and automating corrective actions. The
project bundles asynchronous price polling, reserve orchestration, repayment utilities, simulation tooling, and a human-friendly
dashboard so teams can keep liquidation risk under control.

The repository still contains the original Gatsby starter site, but the active code lives in the `loan_monitor/` Python package
and the accompanying test suite under `tests/`.

## Features

- **Config-driven setup** – Centralise API keys, loan balances, collateral holdings, thresholds, and platform terms in
  `config.yaml` or via `LOAN_MONITOR_CONFIG`, with support for multiple loan profiles and a selectable default.
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
- **Web dashboard & API** – A FastAPI service exposes authenticated endpoints for snapshots, history, and reserve actions, while
  the Gatsby UI at `/dashboard` visualises LTV trends, buffers, and remediation controls for profile operators.
- **Multi-exchange diversification** – Register Binance and Nexo accounts, aggregate balances in the dashboard, and enforce
  withdrawal guardrails that keep portfolio LTV below safe thresholds.
- **Encrypted secrets manager** – Store API keys and credentials in a passphrase-protected vault with environment-specific
  overrides and a CLI for rotation.
- **Structured logging** – JSON log helpers make it simple to forward operational events into log pipelines.
- **Role-based security** – JWT tokens issued after TOTP verification guard reserve transfers and repayments with trader/admin roles.
- **Audit trail** – Sensitive operations (reserve transfers, repayments, exchange activity) are recorded in an immutable
  `audit_log` table.
- **Observability hooks** – Optional Prometheus metrics expose price latency, health gauges, and alert counters for dashboards, with sample alert rules under `ops/`.
- **Historical analytics** – Every LTV evaluation is stored, enabling CLI summaries, CSV exports, and trend diagnostics for audit reviews.
- **Optional hedging module** – Gated behind user consent, the hedging toolkit validates knowledge checks, prices protective puts
  and short futures (with Black–Scholes fallback), charts payoff curves, and records every quote or execution in an immutable
  audit log.

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
   alert thresholds, notification channels, platform terms, and the `security` block with user roles and TOTP secrets. Define
   one or more entries under `profiles` and set `default_profile` to the profile you want commands to target when `--profile`
   is omitted. The configuration loader will create a SQLite database at `loan_monitor/loan_monitor.db` on first run.
   Environment-specific overrides load from `config.<env>.yaml` when `LOAN_MONITOR_ENV` is set.
4. **Initialise secrets (optional but recommended)** – Encrypt API keys and exchange credentials before running the monitor:
   ```bash
   python -m loan_monitor.cli secrets --env production init
   python -m loan_monitor.cli secrets --env production set api_keys.binance "<api-key>"
   python -m loan_monitor.cli secrets --env production set exchanges.binance.api_secret "<secret>"
   ```
   Provide the same passphrase at runtime via `LOAN_MONITOR_SECRET_PASSPHRASE` or the `--passphrase` flag. Use
   `loan-monitor secrets list` to audit stored keys.
5. **Generate an authentication token** – Use the configured TOTP secret with the CLI to obtain a JWT for state-changing
   operations:
   ```bash
   python -m loan_monitor.cli auth --user trader --totp 123456
   export LOAN_MONITOR_TOKEN="<token>"
   ```
   Tokens expire after `token_ttl_seconds`; rerun the command when prompted.
6. **Initialise the database** – The schema is created automatically when any command touches the database. To pre-create it you
   can run:
   ```bash
   python - <<'PY'
   from loan_monitor.db import get_connection
   get_connection().close()
   PY
   ```
7. **Run tests** to verify the environment:
   ```bash
   pytest
   ```

## Documentation Site

MkDocs powers the documentation in the `docs/` directory. Build or serve the
site locally with:

```bash
mkdocs serve
```

The navigation covers the usage guide, architecture reference, observability
playbooks, CI/CD pipeline, and troubleshooting tips.

## Configuration Reference

`config.yaml` governs runtime behaviour. A minimal multi-profile example looks like:

```yaml
api_keys:
  binance: ""
  coingecko: ""
default_profile: core
profiles:
  - id: core
    name: Core Loan
    loan:
      principal: 1000.0
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
    policy:
      type: auto_topup
      topup_percent: 0.5
      repay_percent: 0.25
    notification_channels:
      - console
    exchanges:
      - binance-main
  - id: conservative
    name: Conservative Book
    loan:
      principal: 500.0
    collateral:
      btc: 0.3
      usdt: 400.0
    reserves:
      btc: 0.05
      usdt: 200.0
    thresholds:
      warning: 0.75
      margin_call: 0.82
      liquidation: 0.88
    policy:
      type: manual
    notification_channels:
      - console
exchanges:
  - name: binance-main
    platform: binance
    collateral:
      btc: 0.5
      usdt: 250.0
    loan_outstanding: 400.0
    safe_withdrawal_ltv: 0.3
    profile: core
  - name: nexo-yield
    platform: nexo
    collateral:
      btc: 0.2
      usdt: 150.0
    loan_outstanding: 150.0
    safe_withdrawal_ltv: 0.32
    profile: conservative
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
hedging:
  enabled: false
  require_consent: true
  consent_prompt: "Derivatives can lose more than the premium paid. Proceed only if you understand the risks."
  consent_version: "v1"
  knowledge_check:
    - id: risk
      prompt: "What is the maximum loss when purchasing a protective put?"
      correct_answers:
        - "premium"
        - "the option premium"
    - id: leverage
      prompt: "Can futures losses exceed collateral posted?"
      correct_answers:
        - "yes"
  allowed_strategies:
    - put_option
    - short_futures
  paper_trading: true
  default_implied_vol: 0.6
  risk_free_rate: 0.02
  futures_margin_ratio: 0.1
  max_notional: 5000.0
  ```

  - **default_profile** selects which profile the CLI targets when `--profile` is omitted.
  - **profiles** encapsulate per-loan data (principal, collateral, reserves, notification channels, policy, thresholds) while
    reusing any top-level defaults you choose to keep.
  - **thresholds** drive alerting for the monitoring loop and the dashboard status classification.
- **exchanges** enumerate the exchange connectors, including initial collateral, per-platform loan balances, and safe withdrawal guardrails.
- **policy** controls reserve behaviour: `auto_topup`, `auto_repay`, or `manual`.
- **terms** captures official exchange terms surfaced in the dashboard for fast auditing.
- **security** defines JWT/TOTP parameters and user roles. Generate TOTP codes with authenticator apps and keep secrets private.
- **observability** toggles the Prometheus metrics server and sets the scrape port.
- **web** configures the FastAPI host, port, and CORS origins powering the interactive dashboard.
- Secrets can be sourced from environment variables if you prefer to keep API keys out of the repository.

Security-sensitive CLI actions append JSON payloads to the `audit_log` SQLite table for downstream compliance review.

## CLI Usage

The toolkit exposes a unified CLI via `python -m loan_monitor.cli` or by installing the package and running `loan-monitor`.
All profile-aware commands accept an optional `--profile <id>` argument and fall back to `default_profile` when omitted.

| Command | Purpose |
| --- | --- |
| `show [--profile <id>]` | Display pledged and unpledged balances tracked by the reserve manager for a loan profile. |
| `auth --user <name> --totp <code>` | Exchange a valid TOTP code for a short-lived JWT token. |
| `transfer <asset> <amount> to_collateral\|to_reserve [--profile <id>] [--token]` | Move funds between reserve pools (requires trader token). |
| `repay <amount> [--dry-run] [--profile <id>] [--token]` | Validate and execute a repayment against the loan principal (requires trader token). |
| |
| `exchange balances [--json] [--profile <id>]` | Summarise per-exchange collateral, outstanding loans, and platforms for a profile. |
| `exchange add --name <id> --asset btc\|usdt --amount <value> [--profile <id>] [--token]` | Credit collateral to a specific exchange connector (requires trader token). |
| `exchange repay --name <id> --amount <value> [--profile <id>] [--token]` | Pay down an exchange-specific loan balance (requires trader token). |
| |
| `exchange withdraw --name <id> --asset btc\|usdt --amount <value> --confirm [--profile <id>] [--token]` | Withdraw collateral after confirming the safe-LTV guardrail (requires admin token). |
| `plan --btc-price <price> [--loan <amount>] [--target-ltv <ratio>] [--profile <id>]` | Calculate BTC/USDT collateral required for a target starting LTV. |
| `simulate --btc-price <price> --drops <...> [--profile <id>]` | Model LTV paths for multiple collateral mixes across price drops. |
| `stress --btc-price <price> --drops <...> [--profile <id>]` | Stress-test extreme moves and compute remediation (top-up / repay) actions. |
| `dashboard [--json] [--profile <id>]` | Render a consolidated snapshot of LTV, collateral, platform terms, exchange coverage, and recommended actions. |
| `hedge consent --user <name> --answers <json> --acknowledge [--token]` | Record hedging consent after completing the knowledge check. |
| `hedge quote --type <put|futures> ... [--token]` | Generate a hedging quote with payoff analysis and optional plotting. |
| `hedge execute --type <put|futures> ... --confirm [--token]` | Execute (or paper trade) a hedge with explicit confirmation. |
| `hedge audit [--limit N] [--token]` | Review recent hedging audit log entries (admin token required). |

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

## Web Dashboard

The FastAPI service in `loan_monitor.web` powers the interactive dashboard at `/dashboard` inside the Gatsby front end.

1. **Start the API** – launch the service with uvicorn and an appropriate configuration:

   ```bash
   uvicorn loan_monitor.web:app --host 0.0.0.0 --port 8000 --reload
   ```

   The `web` section in `config.yaml` controls the default host, port, and allowed CORS origins. Override values at runtime with
   environment variables such as `LOAN_MONITOR_CONFIG` or by supplying a custom config file.

2. **Authenticate for a token** – generate a JWT using the CLI `auth` command or the dashboard sign-in form. Both flows require a
   valid username and TOTP code defined under `security.users`.

3. **Serve the dashboard** – from the repository root, run `npm install` (first time only) and then start the Gatsby dev server:

   ```bash
   npm run start
   ```

   By default the dashboard expects the API at `http://localhost:8000`. Change this by exporting `GATSBY_API_BASE_URL` before
   running Gatsby or by defining it in a `.env` file.

The dashboard surfaces current LTV, BTC pricing, collateral composition, headroom to the margin-call threshold, the last few
historical observations (with a sparkline), and quick actions for repayments or reserve transfers. Auto-refresh can be toggled on a
15-second interval for near-real-time monitoring.

## Hedging (Opt-In)

Hedging commands remain inactive until `hedging.enabled` is set to `true` in configuration. Once enabled:

1. **Complete the knowledge check** – Traders must acknowledge the configured consent prompt and answer each question correctly:
   ```bash
   python -m loan_monitor.cli hedge consent --user trader \
       --answers '{"risk": "premium", "leverage": "yes"}' \
       --acknowledge --token "$LOAN_MONITOR_TOKEN"
   ```
2. **Generate quotes** – Price protective puts or short futures and optionally persist a payoff chart:
   ```bash
   python -m loan_monitor.cli hedge quote --type put --strike 25000 --expiry-days 30 \
       --size 0.5 --token "$LOAN_MONITOR_TOKEN" --payoff-file put.png
   ```
   Quotes fall back to a Black–Scholes model when no external quote API is configured and report breakeven, max loss, and sample
   payoff points.
3. **Execute with confirmation** – Trades require `--confirm` and respect role-based controls (live trades demand an admin token
   when `paper_trading` is `false`):
   ```bash
   python -m loan_monitor.cli hedge execute --type futures --size 1.0 --confirm --token "$LOAN_MONITOR_TOKEN"
   ```
4. **Audit the activity** – Compliance teams can inspect immutable records stored in SQLite:
   ```bash
   python -m loan_monitor.cli hedge audit --limit 20 --token "$ADMIN_TOKEN"
   ```

Consent responses and trade actions persist across restarts via the `hedging_consent` and `hedging_audit` tables so policy checks
survive process failures.

## Observability & Deployment

- **Prometheus metrics** – Set `observability.enable_metrics: true` in `config.yaml` to expose counters and gauges on
  `metrics_port` (default `9000`). Scrape the `/metrics` endpoint with Prometheus or another collector to monitor price latency,
  alert counts, and recent LTV values.
- **Health gauges & alerts** – `loan_monitor_health_status` and
  `loan_monitor_last_success_timestamp` capture monitoring health. Import the
  sample rules in `ops/prometheus-alerts.yaml` to notify operators when checks
  fail for more than five minutes or when price lookups repeatedly error.
- **CI/CD** – GitHub Actions (`.github/workflows/ci.yml`) runs linting, tests,
  documentation builds, and a Docker build on every push or pull request to
  `main`, `master`, or `work` branches.
- **Container image** – Build a runnable image locally with `docker build . -t loan-monitor:latest`. The default command emits a
  JSON dashboard snapshot; override `CMD` or provide environment variables to run alternative workflows inside the container.

## Documentation

Detailed usage notes and operational guidance live in the `docs/` directory:

- [`docs/architecture.md`](docs/architecture.md) – component responsibilities, data flow, and persistence model.
- [`docs/usage.md`](docs/usage.md) – end-to-end CLI workflows for monitoring, reserve management, repayment, simulations, the dashboard, and hedging consent/execution.
- [`docs/observability.md`](docs/observability.md) – logging strategy, Prometheus metrics, and alerting rules.
- [`docs/ci_cd.md`](docs/ci_cd.md) – workflow overview, local verification steps, and deployment notes.
- [`docs/troubleshooting.md`](docs/troubleshooting.md) – guidance for common setup and runtime issues.

Serve the collection with `mkdocs serve` to publish browsable internal documentation.

## Testing

Run the automated test suite before committing changes:

```bash
pytest
ruff check loan_monitor tests
```

Tests cover price fetching, LTV computations, reserve policies, repayment validation, simulation helpers, the dashboard
snapshot builder, and hedging consent/pricing logic.

## Troubleshooting & Tips

- **Network sandboxing:** Price fetching requires outbound HTTPS access. When running offline, the API and dashboard will reuse
  the most recent cached BTC price recorded in `ltv_history` or `collateral_snapshot`. For a brand-new database with no cached
  price, monkeypatch `PriceService.get_price` in your scripts or rely on the test doubles provided in the suite.
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
