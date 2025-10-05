from prometheus_client import CollectorRegistry

from loan_monitor.metrics import _build_metrics


def test_metrics_health_transitions():
    registry = CollectorRegistry()
    metrics = _build_metrics(registry)

    # Failure should drop the health gauge to 0
    metrics.record_check_failure("profile-a")
    assert metrics.health_status.labels(profile="profile-a")._value.get() == 0

    # Success should raise the gauge and update timestamp
    metrics.record_check_success("profile-a", timestamp=123.0)
    assert metrics.health_status.labels(profile="profile-a")._value.get() == 1
    assert (
        metrics.last_success_timestamp.labels(profile="profile-a")._value.get() == 123.0
    )
