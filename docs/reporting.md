# Historical Reporting & Analytics

The monitoring loop persists every price check and resulting loan-to-value (LTV) calculation in the `ltv_history` table. These
records underpin time-series analytics, audit trails, and CSV exports.

## Captured Fields

Each entry stores:

- Timestamp of the observation (`created_at`).
- Computed LTV ratio.
- Aggregated BTC and USDT collateral at the time of evaluation.
- BTC reference price, principal, and accrued interest used for the calculation.
- Alert level (`none`, `warning`, `margin_call`, `liquidation`) that applied when the measurement was taken.

## CLI Usage

Generate a summary for the default profile:

```bash
python -m loan_monitor.cli report ltv --hours 12
```

The CLI outputs:

- Statistical overview (average, min/max, standard deviation).
- LTV change across the selected window and a short-term trend indicator.
- Counts of warning, margin-call, and liquidation events encountered.

Add `--json` to receive the same payload in machine-readable form, or `--csv history.csv` to create a spreadsheet-ready export.

## Working with Multiple Profiles

Pass `--profile <id>` to focus on a specific loan book. The reporting service honours the same profile resolution as the
monitoring, dashboard, and reserve managers.

## Downstream Analysis

- Feed the CSV output into BI tooling to visualise LTV distribution over time.
- Compare the `recent_trend` indicator with alert counts to identify periods of elevated stress.
- Combine with the audit log to correlate policy actions (top-ups, repayments) with subsequent LTV improvements.

Historical analytics are designed to complement the dashboard snapshots, providing longer-term context and evidence for risk
committee reviews.
