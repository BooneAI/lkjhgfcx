import json
import logging

from loan_monitor.logging_setup import JsonFormatter


def test_json_formatter_includes_extra_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="loan_monitor.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="event",
        args=(),
        exc_info=None,
    )
    record.user = "alice"
    record.event = "margin_call"

    payload = json.loads(formatter.format(record))
    assert payload["message"] == "event"
    assert payload["user"] == "alice"
    assert payload["event"] == "margin_call"
