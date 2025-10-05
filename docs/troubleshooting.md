# Troubleshooting

Common pitfalls and fixes when operating the `loan_monitor` toolkit.

## Price Fetching Errors

- **HTTP timeouts** – The price service defaults to a 10 second timeout. If you see repeated failures, confirm outbound network
  access and consider increasing the timeout or caching interval when instantiating `PriceService(ttl=<seconds>)`.
- **API throttling** – CoinGecko rate limits aggressive polling. The default 10-minute cache (TTL) keeps within published limits;
  reduce the polling frequency via `poll_interval` to stay compliant.
- **Offline environments** – The API and dashboard reuse the latest cached BTC price stored in `ltv_history`/`collateral_snapshot`
  when live sources are unreachable. If you are bootstrapping a fresh database with no cached entries, monkeypatch
  `PriceService.get_price` (as done in the unit tests) or provide an alternative service that returns fixture prices.

## Database Issues

- **Locked database file** – SQLite locks the database during writes. Run long-lived monitoring processes as a single instance or
  move to a client/server database (PostgreSQL) if you require concurrent writers.
- **Missing tables** – `get_connection()` creates the schema automatically. If you encounter `no such table` errors double-check
  that you are opening the same database file. Override the location with the `LOAN_MONITOR_DB_PATH` environment variable.
- **Resetting state** – Delete `loan_monitor/loan_monitor.db` (or your custom path) to start clean. Be aware this clears historical
  collateral snapshots.

## Configuration Problems

- **Invalid YAML** – Run `python - <<'PY'` with `yaml.safe_load(open("config.yaml"))` to validate syntax before launching the
  monitor.
- **Threshold confusion** – `thresholds` drive alerting logic while `terms` documents platform defaults. Keep both in sync to
  avoid conflicting messaging.
- **Secrets management** – Use the built-in `loan-monitor secrets` vault to encrypt API keys. Provide the passphrase via
  `LOAN_MONITOR_SECRET_PASSPHRASE` or `--passphrase`; keep plain-text config values for non-sensitive defaults only.

## CLI Usage

- **No command specified** – Running `python -m loan_monitor.cli` without arguments prints nothing. Run `python -m loan_monitor.cli
  --help` to list available subcommands.
- **JSON serialisation** – `dashboard --json` returns floating-point values. Use `jq` or your preferred JSON processor to format the
  output for dashboards.

## Web Dashboard

- **401/`Session expired` banner** – JWTs are short-lived. Re-authenticate with a fresh TOTP code via the dashboard or the
  `auth` CLI command and ensure the API clock is in sync.
- **CORS errors in the browser console** – Update `web.allowed_origins` in `config.yaml` (or export `GATSBY_API_BASE_URL` to match
  the configured origin) and restart uvicorn so FastAPI refreshes its CORS settings.
- **`fetch failed` network errors** – Confirm the API is running (default `http://localhost:8000`) and that the Gatsby process has
  access to it. When running in containers, forward the API port to the host or update the base URL accordingly.

## Exchange Operations

- **`withdrawal requires explicit --confirm acknowledgement`** – Withdrawals must include `--confirm` so operators explicitly approve the action.
- **`withdrawal blocked: resulting LTV ... exceeds safe threshold`** – The requested withdrawal would breach the per-exchange `safe_withdrawal_ltv`. Reduce the amount or revisit the guardrail after a risk review.
- **`withdrawal would breach margin-call threshold`** – Removing that much collateral would exceed the global `thresholds.margin_call` level; repay or redistribute collateral first.
- **Audit trail checks** – Use `SELECT action, details FROM audit_log ORDER BY id DESC LIMIT 20;` to confirm transfers, repayments, and exchange events were persisted.

## Hedging Consent & Execution

- **`user has not completed hedging consent workflow`** – Run `hedge consent` with the trader’s token, include `--acknowledge`, and answer every knowledge-check question exactly as configured in `config.yaml`.
- **`explicit acknowledgement required`** – The consent command must include the `--acknowledge` flag to affirm the risk warning.
- **`explicit confirmation required for hedging trades`** – All executions require `--confirm` to avoid accidental submissions. Re-run the command with the flag once you have reviewed the quote output.
- **`trade exceeds configured notional limit`** – Reduce the trade size or raise `hedging.max_notional` to stay under the configured cap.
- **`trade_api_url must be configured for live trades`** – When `paper_trading` is `false`, you must supply a real API endpoint for executions; otherwise leave paper trading enabled.

## Authentication Errors

- **`invalid TOTP`** – Confirm the authenticator app is configured with the same base32 secret as `config.yaml`. TOTP codes are time-based; ensure the device clock is in sync (NTP) and regenerate a fresh code.
- **`insufficient role`** – The token’s `role` claim must meet or exceed the required permission (`trader` for transfers/repayments). Issue a token for a user with the appropriate role or adjust the command to a read-only alternative.
- **`invalid token`** – Tokens expire after `token_ttl_seconds`. Run the `auth` command again and update the `LOAN_MONITOR_TOKEN` environment variable.

## Metrics Endpoint

- **Address already in use** – If the Prometheus server cannot bind to the configured port, choose another `observability.metrics_port` or disable metrics locally by setting `enable_metrics: false`.
- **No `/metrics` output** – Metrics are only exposed when `enable_metrics: true`. Restart the monitoring service after changing configuration so the HTTP server is initialised.

## Testing & Development

- **pytest cannot import modules** – The test suite injects the repository root into `sys.path`. If you restructure directories,
  update the path logic at the top of the test files.
- **Async tests hanging** – All async functions in the suite use `asyncio.run(...)` for clarity. Ensure your custom tests follow
  the same pattern or reuse pytest-asyncio fixtures.

## Escalations

If issues persist, capture logs emitted by `loan_monitor.logging_setup.setup_logging()` and include:

1. The command executed and environment configuration (omit secrets).
2. Relevant stack traces or error messages.
3. Recent changes to `config.yaml` or database records.

With that context it becomes much easier to triage problems or extend the toolkit for your organisation’s needs.
