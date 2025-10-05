"""Helpers for recording immutable audit events."""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from .db import get_connection


LOGGER = logging.getLogger(__name__)


def _normalise_details(details: Any) -> str:
    if isinstance(details, str):
        return details
    if isinstance(details, Mapping):
        return json.dumps(details, sort_keys=True)
    return json.dumps(details)


def log_audit_event(action: str, details: Any, *, user: str | None = None, conn=None) -> None:
    """Persist a security-sensitive action to the audit log."""

    conn = conn or get_connection()
    payload = _normalise_details(details)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO audit_log(user, action, details) VALUES(?, ?, ?)",
        (user, action, payload),
    )
    conn.commit()
    LOGGER.info("audit event %s user=%s", action, user or "system")


__all__ = ["log_audit_event"]
