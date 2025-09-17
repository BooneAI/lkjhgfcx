"""Configuration loader for loan monitor service."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import yaml


@dataclass
class Thresholds:
    warning: float = 0.80
    margin_call: float = 0.85
    liquidation: float = 0.91


@dataclass
class Terms:
    """Static platform terms displayed in dashboards and documentation."""

    platform: str = "Binance Flexible Loan"
    initial: float = 0.78
    warning: float = 0.80
    margin_call: float = 0.85
    liquidation: float = 0.91
    liquidation_fee: float = 0.02
    last_reviewed: str | None = None
    documentation_url: str | None = None
    notes: str | None = None


@dataclass
class Config:
    api_keys: dict
    loan: dict
    collateral: dict
    thresholds: Thresholds
    poll_interval: int = 600
    notification_channels: list[str] | None = None
    reserves: dict | None = None
    policy: dict | None = None
    terms: Terms = field(default_factory=Terms)


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path: Path | None = None) -> Config:
    """Load YAML configuration file into a Config object."""
    cfg_path = path or Path(os.environ.get("LOAN_MONITOR_CONFIG", _DEFAULT_CONFIG_PATH))
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    thresholds = Thresholds(**raw.get("thresholds", {}))
    terms = Terms(**raw.get("terms", {}))

    return Config(
        api_keys=raw.get("api_keys", {}),
        loan=raw.get("loan", {}),
        collateral=raw.get("collateral", {}),
        reserves=raw.get("reserves", {}),
        policy=raw.get("policy", {}),
        thresholds=thresholds,
        poll_interval=raw.get("poll_interval", 600),
        notification_channels=raw.get("notification_channels", []),
        terms=terms,
    )


__all__ = ["Config", "Thresholds", "Terms", "load_config"]
