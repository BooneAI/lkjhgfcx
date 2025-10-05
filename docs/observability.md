# Observability & Alerting

This guide explains how to instrument the monitoring service, consume the JSON
logs, and wire Prometheus alerts so that operational teams can react quickly to
risk events.

## Structured Logging

`loan_monitor.logging_setup.setup_logging()` configures a JSON formatter that
writes log lines such as:

```json
{"level": "INFO", "time": "2024-01-01T12:00:00", "name": "loan_monitor.services.monitor", "message": "margin_call alert dispatched"}
```

Forward these logs to your preferred aggregation pipeline (e.g. Loki, Elastic,
or CloudWatch) and build dashboards that highlight alert frequency, reserve
movements, and hedging activity. When emitting logs you can include extra
context via the `extra` argument in the standard logging API; the formatter will
serialise those fields alongside the default keys.

## Prometheus Metrics

The package exposes Prometheus metrics via `loan_monitor.metrics`. When
`observability.enable_metrics` is true in `config.yaml` the monitor launches a
metrics HTTP server on `observability.metrics_port` (default 9000). Key metrics
include:

| Metric | Type | Description |
| ------ | ---- | ----------- |
| `loan_monitor_ltv_ratio` | Gauge | Latest computed LTV value. |
| `loan_monitor_alerts_total` | Counter | Number of alerts emitted, labelled by level (warning, margin_call, liquidation). |
| `loan_monitor_price_fetch_seconds` | Histogram | Latency distribution for price lookups per source. |
| `loan_monitor_price_failures_total` | Counter | Count of failed price fetches, labelled by source. |
| `loan_monitor_check_failures_total` | Counter | Monitoring loop iterations that failed due to unexpected errors. |
| `loan_monitor_health_status` | Gauge | `1` when the most recent monitoring cycle succeeded, `0` after failures. |
| `loan_monitor_last_success_timestamp` | Gauge | Unix timestamp for the last successful monitoring run. |

Scrape the metrics endpoint from Prometheus and feed the series into Grafana or
another dashboarding tool. The health gauges make it straightforward to alert on
stalled monitoring loops or a prolonged inability to fetch prices.

## Alerting Rules

Sample Prometheus alerting rules are provided in
`ops/prometheus-alerts.yaml`. Import the rules into Alertmanager to receive
notifications when:

- The monitoring loop has not produced a successful check for more than five
  minutes.
- Price fetches have failed repeatedly within a short window (customisable via
  the rule expressions).

### Deploying the Alerts

1. Copy `ops/prometheus-alerts.yaml` into your infrastructure repository.
2. Reference the rule file from your Prometheus configuration, for example:

   ```yaml
   rule_files:
     - "ops/prometheus-alerts.yaml"
   ```

3. Configure Alertmanager receivers (email, Slack, PagerDuty) to route the new
   alerts to the right team.

## Operational Dashboards

Combine the Prometheus metrics with the JSON dashboard output from
`python -m loan_monitor.cli dashboard --json` to build Grafana panels showing:

- Current LTV and distance to thresholds.
- Reserve balances and pending top-ups.
- Hedge coverage, including outstanding option and futures exposures.
- Frequency of alerts per level.

Dashboards should surface the same static platform terms displayed by the CLI so
operators can quickly validate that Binance (or other exchanges) has not changed
its margin policies.
