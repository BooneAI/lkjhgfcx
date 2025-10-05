"""Prometheus metrics helpers for the loan monitor service."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)


@dataclass
class Metrics:
    """Container of metrics primitives with convenience helpers."""

    registry: CollectorRegistry
    ltv_gauge: Gauge
    alert_counter: Counter
    price_latency: Histogram
    price_failures: Counter
    check_failures: Counter
    health_status: Gauge
    last_success_timestamp: Gauge

    def observe_price_fetch(self, source: str, latency: float, success: bool) -> None:
        self.price_latency.labels(source=source).observe(latency)
        if not success:
            self.price_failures.labels(source=source).inc()

    def update_ltv(self, profile: str, value: float) -> None:
        self.ltv_gauge.labels(profile=profile).set(value)

    def record_alert(self, profile: str, level: str) -> None:
        self.alert_counter.labels(profile=profile, level=level).inc()

    def record_check_success(self, profile: str, timestamp: float | None = None) -> None:
        ts = timestamp if timestamp is not None else time.time()
        self.health_status.labels(profile=profile).set(1)
        self.last_success_timestamp.labels(profile=profile).set(ts)

    def record_check_failure(self, profile: str) -> None:
        self.check_failures.labels(profile=profile).inc()
        self.health_status.labels(profile=profile).set(0)


_metrics: Optional[Metrics] = None
_metrics_lock = threading.Lock()
_metrics_server_started = False


def _build_metrics(registry: CollectorRegistry | None = None) -> Metrics:
    reg = registry or REGISTRY
    return Metrics(
        registry=reg,
        ltv_gauge=Gauge(
            "loan_monitor_ltv_ratio",
            "Latest computed loan-to-value ratio",
            labelnames=["profile"],
            registry=reg,
        ),
        alert_counter=Counter(
            "loan_monitor_alerts_total",
            "Number of alerts emitted",
            labelnames=["profile", "level"],
            registry=reg,
        ),
        price_latency=Histogram(
            "loan_monitor_price_fetch_seconds",
            "Latency for external price lookups",
            labelnames=["source"],
            registry=reg,
        ),
        price_failures=Counter(
            "loan_monitor_price_failures_total",
            "Count of failed price lookups by source",
            labelnames=["source"],
            registry=reg,
        ),
        check_failures=Counter(
            "loan_monitor_check_failures_total",
            "Number of monitoring loop errors",
            labelnames=["profile"],
            registry=reg,
        ),
        health_status=Gauge(
            "loan_monitor_health_status",
            "Boolean gauge indicating if the last monitoring cycle succeeded",
            labelnames=["profile"],
            registry=reg,
        ),
        last_success_timestamp=Gauge(
            "loan_monitor_last_success_timestamp",
            "Unix timestamp for the last successful monitoring cycle",
            labelnames=["profile"],
            registry=reg,
        ),
    )


def get_metrics() -> Metrics:
    """Return the process-wide metrics container, creating it on first use."""

    global _metrics
    if _metrics is None:
        with _metrics_lock:
            if _metrics is None:
                _metrics = _build_metrics()
    return _metrics


def init_metrics_server(port: int = 9000) -> None:
    """Start an HTTP server that exposes Prometheus metrics if not running."""

    global _metrics_server_started
    get_metrics()  # ensure metrics exist
    with _metrics_lock:
        if not _metrics_server_started:
            start_http_server(port)
            _metrics_server_started = True


__all__ = ["Metrics", "get_metrics", "init_metrics_server", "_build_metrics"]
