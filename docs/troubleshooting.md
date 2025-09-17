# Troubleshooting

Common pitfalls and fixes when operating the `loan_monitor` toolkit.

## Price Fetching Errors

- **HTTP timeouts** – The price service defaults to a 10 second timeout. If you see repeated failures, confirm outbound network
  access and consider increasing the timeout or caching interval when instantiating `PriceService(ttl=<seconds>)`.
- **API throttling** – CoinGecko rate limits aggressive polling. The default 10-minute cache (TTL) keeps within published limits;
  reduce the polling frequency via `poll_interval` to stay compliant.
- **Offline environments** – Monkeypatch `PriceService.get_price` (as done in the unit tests) or provide an alternative service
  that returns fixture prices.

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
- **Secrets management** – Store API keys in environment variables or a secrets manager. You can still define non-sensitive
  defaults in `config.yaml` for local development.

## CLI Usage

- **No command specified** – Running `python -m loan_monitor.cli` without arguments prints nothing. Run `python -m loan_monitor.cli
  --help` to list available subcommands.
- **JSON serialisation** – `dashboard --json` returns floating-point values. Use `jq` or your preferred JSON processor to format the
  output for dashboards.

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
