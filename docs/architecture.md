# Architecture Overview

This document explains how the `loan_monitor` package is structured, how state flows through the system, and where to extend the
codebase as you add security, observability, or dashboarding layers.

## High-Level Components

| Layer | Responsibility |
| --- | --- |
| **Configuration** | `loan_monitor.config` loads YAML (or `LOAN_MONITOR_CONFIG`) into typed dataclasses. The loader resolves `default_profile`, merges per-profile overrides, and centralises thresholds and platform terms. |
| **Secrets Vault** | `loan_monitor.secrets.SecretManager` encrypts API keys and credentials with a passphrase-derived key and merges them into runtime configuration. |
| **Persistence** | `loan_monitor.db` provisions a SQLite database storing the active loan balance, collateral snapshots, and reserve balances for each profile. The schema is created automatically when a connection is opened. |
| **Price Ingestion** | `loan_monitor.services.pricing.PriceService` asynchronously queries Binance and CoinGecko with caching + failover so that transient exchange issues do not break monitoring. |
| **LTV Calculations** | `loan_monitor.services.ltv` defines the `LoanState` dataclass, `compute_ltv`, and the new `price_drop_to_reach_ltv` helper used in planning, simulations, and the dashboard. |
| **Monitoring Loop** | `loan_monitor.services.monitor.LTVMonitor` orchestrates the price service, pulls persisted state, enforces cooldowns, and delegates notifications and reserve policies when thresholds are breached. |
| **Reserve Management** | `loan_monitor.services.reserve.ReserveManager` maintains pledged vs. unpledged balances, exposes transfer helpers, and executes policy hooks triggered by `LTVMonitor`. |
| **Exchange Connectors** | `loan_monitor.exchanges.ExchangeManager` provisions per-platform connectors (Binance, Nexo), aggregates collateral across venues, and enforces withdrawal guardrails with audit logging. |
| **Repayment Module** | `loan_monitor.services.repayment.RepaymentService` validates amounts, optionally calls exchange APIs, updates principal, and recalculates LTV (with dry-run support). |
| **Simulation Suite** | `loan_monitor.services.simulations` hosts collateral planning, volatility modelling, and stress-test utilities. These reuse `compute_ltv` to stay consistent with live monitoring. |
| **Dashboard** | `loan_monitor.services.dashboard` (added in Step 6) assembles a snapshot that blends live LTV metrics, collateral state, and static platform terms for CLI or JSON consumption. |
| **Notifications** | `loan_monitor.notifications` defines the base notifier interface; concrete implementations (console, Slack, email) can be registered in `config.yaml`. |
| **Security** | `loan_monitor.security.AuthManager` validates TOTP codes, issues JWTs, and checks role-based permissions for write operations. |
| **Observability** | `loan_monitor.metrics` exposes Prometheus counters and gauges (price latency, alert totals, monitoring errors) with an optional HTTP endpoint. |
| **Hedging** | `loan_monitor.services.hedging.HedgingService` enforces consent, prices protective puts and short futures with fallbacks, generates payoff analyses, and records audit logs. |

## Data Flow

1. **Configuration load** – An entrypoint (CLI or service) calls `load_config`, producing `Config`, `Thresholds`, and `Terms` instances along with a dictionary of `LoanProfileSettings` keyed by profile ID.
2. **Database connection** – `get_connection` initialises (or reuses) the SQLite database storing loan, collateral, and reserve data.
3. **Price fetch** – `PriceService.get_price()` asynchronously queries Binance, falling back to CoinGecko on error. Successful responses are cached for `ttl` seconds.
4. **State composition** – `LoanState` pulls the latest loan and collateral rows for the selected profile; defaults from that profile fill in gaps on first run.
5. **Computation** – `compute_ltv` derives the active LTV. `price_drop_to_reach_ltv` solves for the BTC drawdown that would hit the profile's margin-call threshold.
6. **Decisioning** – `LTVMonitor` compares LTV to thresholds. If a level is breached, it emits notifications and optionally calls `ReserveManager.apply_policy` to top up or repay automatically for that profile.
7. **Dashboard snapshot** – `gather_dashboard_snapshot` consolidates current LTV, collateral valuations, threshold status, recommended actions, exchange coverage, and the static `terms` section scoped to the chosen profile.
8. **CLI output** – The CLI renders human-readable text via `render_dashboard` or emits structured JSON for automation pipelines, defaulting to `default_profile` unless `--profile` is supplied.

## CLI Entry Points

`loan_monitor/cli.py` is the thin orchestration layer connecting user commands to services. Every command follows the same
pattern:

1. Load configuration (`load_config`), which automatically merges environment overlays and decrypted secrets.
2. Resolve the active profile (`cfg.get_profile(...)`) using `--profile` or `cfg.default_profile`.
3. Ensure the database schema exists (`ReserveManager` or explicit connections).
4. For state-changing commands, validate the JWT (from `loan_monitor.security`) before calling into services.
5. Run the requested service (reserve transfer, repayment, simulation, or dashboard) and print the results.

The dedicated `secrets` subcommands operate slightly differently: they resolve the configuration/secrets paths first, prompt for a passphrase when needed, and then read or mutate the encrypted secrets file.

This keeps business logic in reusable services so your own applications can import them directly without going through the CLI.

## Persistence Model

```
loan
└─ profile (primary key)
   ├─ principal (current outstanding principal)
   ├─ interest (accrued interest)
   └─ updated_at (auto timestamp)

collateral_snapshot
└─ rolling history of pledged collateral per profile
   ├─ profile
   ├─ btc_amount
   ├─ usdt_amount
   ├─ btc_price (price used when the snapshot was captured)
   └─ created_at

reserves
└─ (profile, asset) view of pledged/unpledged balances supporting policy actions

hedging_consent
└─ user responses to the derivatives knowledge check (one row per user/version)

hedging_audit
└─ append-only log of hedging quotes, executions, and consent acknowledgements

exchange_positions
└─ per-exchange collateral and outstanding loan balances (used to aggregate totals)

audit_log
└─ immutable security log capturing transfers, repayments, and exchange activity

ltv_history
└─ append-only record of every monitor evaluation (ltv, collateral, debt, alert level)
```

Snapshots are append-only, enabling historical analysis. `ReserveManager` keeps the `reserves` table in sync each time funds move
between pledged and unpledged pools for a profile, while `HedgingService` relies on the consent/audit tables to enforce user opt-ins across
process restarts. The `ReportingService` reads `ltv_history` to build rollups for dashboards, CSV exports, and governance reports.

## Extensibility Hooks

- **Notifications** – Implement `loan_monitor.notifications.base.Notifier` to route alerts to Slack, email, PagerDuty, etc.
- **Policies** – Extend the policy runner in `ReserveManager.apply_policy` to support hybrid strategies (e.g. partial repay + top-up).
- **Dashboard & API** – `loan_monitor.web.create_app` wraps `DashboardSnapshot` and reporting services in a FastAPI layer that the
  Gatsby dashboard consumes. Extend or replace routes to integrate with other UIs.
- **Security** – Extend `loan_monitor.security.AuthManager` to back user stores (e.g. PostgreSQL) or hook into SSO providers. Audit logging can be added alongside JWT issuance.
- **Observability** – `loan_monitor.logging_setup.setup_logging()` emits JSON logs while `loan_monitor.metrics` publishes Prometheus metrics; wire them into your monitoring stack for alerting and dashboards.
- **Hedging** – Swap in alternative quote providers or extend `HedgingService` with additional strategies by reusing the consent
  and audit helpers provided here.

## Related Documentation

- [`docs/usage.md`](./usage.md) walks through day-to-day workflows with sample commands and expected output.
- [`docs/troubleshooting.md`](./troubleshooting.md) captures known failure modes and remediation tips.

With these building blocks—and the bundled FastAPI + Gatsby dashboard—you can confidently extend the system with additional services such as custom routes, scheduled jobs, or downstream analytics.
