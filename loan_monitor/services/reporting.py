"""Reporting helpers for historical LTV analytics."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import statistics

from ..config import Config
from ..db import get_connection


def _to_iso(timestamp: str | bytes | float | int | None) -> str:
    """Normalise SQLite timestamps into ISO 8601 strings."""

    if timestamp is None:
        return ""
    if isinstance(timestamp, bytes):
        timestamp = timestamp.decode("utf-8")
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(float(timestamp)).isoformat()
    if isinstance(timestamp, str):
        candidate = timestamp.replace(" ", "T")
        try:
            return datetime.fromisoformat(candidate).isoformat()
        except ValueError:
            try:
                return datetime.fromisoformat(timestamp).isoformat()
            except ValueError:
                return timestamp
    return str(timestamp)


@dataclass
class LTVHistoryEntry:
    """Row captured every time the monitor evaluates LTV."""

    created_at: str
    ltv: float
    btc_amount: float
    usdt_amount: float
    btc_price: float
    principal: float
    interest: float
    alert_level: str

    @property
    def collateral_value(self) -> float:
        return self.btc_amount * self.btc_price + self.usdt_amount

    @property
    def debt(self) -> float:
        return self.principal + self.interest

    def to_dict(self) -> dict[str, float | str]:
        return {
            "created_at": self.created_at,
            "ltv": self.ltv,
            "btc_amount": self.btc_amount,
            "usdt_amount": self.usdt_amount,
            "btc_price": self.btc_price,
            "principal": self.principal,
            "interest": self.interest,
            "alert_level": self.alert_level,
            "collateral_value": self.collateral_value,
            "debt": self.debt,
        }


class ReportingService:
    """Expose historical analytics for dashboards and operators."""

    def __init__(self, config: Config, conn=None) -> None:
        self.config = config
        self.conn = conn or get_connection()

    # ------------------------------------------------------------------
    def get_ltv_history(
        self,
        profile_id: str | None = None,
        *,
        limit: int | None = 100,
        hours: float | None = None,
    ) -> list[LTVHistoryEntry]:
        profile = self.config.get_profile(profile_id)
        cur = self.conn.cursor()
        query = (
            "SELECT ltv, btc_amount, usdt_amount, btc_price, principal, "
            "interest, alert_level, created_at FROM ltv_history WHERE profile=?"
        )
        params: list[object] = [profile.id]
        if hours is not None:
            params.append(f"-{float(hours)} hours")
            query += " AND created_at >= datetime('now', ?)"
        query += " ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        cur.execute(query, params)
        rows = cur.fetchall()
        entries: list[LTVHistoryEntry] = []
        for row in reversed(rows):
            entries.append(
                LTVHistoryEntry(
                    created_at=_to_iso(row[7]),
                    ltv=float(row[0]),
                    btc_amount=float(row[1]),
                    usdt_amount=float(row[2]),
                    btc_price=float(row[3]),
                    principal=float(row[4]),
                    interest=float(row[5]),
                    alert_level=str(row[6] or "none"),
                )
            )
        return entries

    def summarize_ltv(
        self,
        profile_id: str | None = None,
        *,
        limit: int | None = 1000,
        hours: float | None = None,
    ) -> dict:
        entries = self.get_ltv_history(profile_id, limit=limit, hours=hours)
        if not entries:
            return {
                "count": 0,
                "average": None,
                "min": None,
                "max": None,
                "stddev": None,
                "change": None,
                "hours_covered": 0.0,
                "levels": {},
                "margin_events": 0,
                "liquidation_events": 0,
                "recent_trend": None,
                "latest": None,
                "earliest": None,
            }

        values = [entry.ltv for entry in entries]
        average = statistics.fmean(values)
        stddev = statistics.pstdev(values) if len(values) > 1 else 0.0
        level_counts = Counter(entry.alert_level for entry in entries)
        earliest = entries[0]
        latest = entries[-1]
        try:
            start = datetime.fromisoformat(earliest.created_at)
            end = datetime.fromisoformat(latest.created_at)
            hours_covered = (end - start).total_seconds() / 3600
        except ValueError:
            hours_covered = 0.0

        recent_trend = latest.ltv - earliest.ltv
        if len(values) >= 6:
            recent = statistics.fmean(values[-3:])
            baseline = statistics.fmean(values[-6:-3])
            recent_trend = recent - baseline

        summary = {
            "count": len(entries),
            "average": average,
            "min": min(values),
            "max": max(values),
            "stddev": stddev,
            "change": latest.ltv - earliest.ltv,
            "hours_covered": hours_covered,
            "levels": dict(level_counts),
            "margin_events": sum(
                level_counts.get(level, 0) for level in ("margin_call", "liquidation")
            ),
            "liquidation_events": level_counts.get("liquidation", 0),
            "recent_trend": recent_trend,
            "latest": latest.to_dict(),
            "earliest": earliest.to_dict(),
        }
        summary["window_limit"] = limit
        summary["window_hours"] = hours
        return summary

    def export_history(
        self,
        path: Path,
        profile_id: str | None = None,
        *,
        limit: int | None = None,
        hours: float | None = None,
    ) -> Path:
        entries = self.get_ltv_history(profile_id, limit=limit, hours=hours)
        fieldnames = [
            "created_at",
            "ltv",
            "btc_amount",
            "usdt_amount",
            "btc_price",
            "principal",
            "interest",
            "alert_level",
            "collateral_value",
            "debt",
        ]
        path = Path(path)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for entry in entries:
                writer.writerow(entry.to_dict())
        return path


__all__ = ["ReportingService", "LTVHistoryEntry"]
