"""Configuration loader for loan monitor service."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
import os
from typing import Any, Dict, Iterable

import yaml

from .security import SecuritySettings, SecurityUser
from .secrets import SecretManager, SecretPassphraseRequired


@dataclass
class Thresholds:
    warning: float = 0.80
    margin_call: float = 0.85
    liquidation: float = 0.91


@dataclass
class ObservabilitySettings:
    enable_metrics: bool = False
    metrics_port: int = 9000


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
class KnowledgeQuestion:
    """Single question used during hedging knowledge checks."""

    id: str
    prompt: str
    correct_answers: list[str]


@dataclass
class HedgingSettings:
    """Configuration toggles for the optional hedging module."""

    enabled: bool = False
    require_consent: bool = True
    consent_prompt: str = (
        "Hedging involves derivatives risk. Confirm you understand the potential for losses."
    )
    consent_version: str = "v1"
    knowledge_check: list[KnowledgeQuestion] = field(default_factory=list)
    allowed_strategies: list[str] = field(
        default_factory=lambda: ["put_option", "short_futures"]
    )
    paper_trading: bool = True
    option_quote_url: str | None = None
    futures_quote_url: str | None = None
    trade_api_url: str | None = None
    default_implied_vol: float = 0.6
    risk_free_rate: float = 0.02
    futures_margin_ratio: float = 0.1
    max_notional: float | None = None


@dataclass
class ExchangeConfig:
    """Connection information for a single exchange account."""

    name: str
    platform: str
    collateral: dict[str, float] = field(default_factory=dict)
    loan_outstanding: float = 0.0
    api_key: str | None = None
    api_secret: str | None = None
    passphrase: str | None = None
    safe_withdrawal_ltv: float = 0.30
    warning_ltv: float | None = None
    profile: str = "default"


@dataclass
class WebSettings:
    """Configuration for the optional HTTP API powering the web UI."""

    host: str = "0.0.0.0"
    port: int = 8000
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost:5000",
            "http://127.0.0.1:5000",
            "http://localhost:8000",
        ]
    )


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
    security: SecuritySettings = field(default_factory=SecuritySettings)
    observability: ObservabilitySettings = field(default_factory=ObservabilitySettings)
    hedging: HedgingSettings = field(default_factory=HedgingSettings)
    web: WebSettings = field(default_factory=WebSettings)
    exchanges: list[ExchangeConfig] = field(default_factory=list)
    profiles: dict[str, "LoanProfileSettings"] = field(default_factory=dict)
    default_profile: str = "default"

    # ------------------------------------------------------------------
    def get_profile(self, profile_id: str | None = None) -> "LoanProfileSettings":
        """Return a normalized profile configuration.

        Parameters
        ----------
        profile_id:
            Identifier of the profile. When omitted the configured default
            profile is returned. A ``ValueError`` is raised when the requested
            profile is unknown.
        """

        profile_key = profile_id or self.default_profile
        try:
            return self.profiles[profile_key]
        except KeyError as exc:  # pragma: no cover - defensive guard
            available = ", ".join(sorted(self.profiles)) or "<none>"
            raise ValueError(
                f"unknown loan profile '{profile_key}'. Available profiles: {available}"
            ) from exc

    def iter_profiles(self) -> Iterable["LoanProfileSettings"]:
        """Iterate over configured profiles in insertion order."""

        for key in self.profiles:
            yield self.profiles[key]


@dataclass
class LoanProfileSettings:
    """Normalised configuration for a single loan profile."""

    id: str
    name: str
    loan: dict
    collateral: dict
    reserves: dict
    policy: dict
    thresholds: Thresholds
    poll_interval: int
    notification_channels: list[str]
    exchanges: list[str]


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def resolve_config_path(path: Path | None = None) -> Path:
    """Resolve the configuration path from CLI arguments or environment."""

    if path is not None:
        return path
    env_override = os.environ.get("LOAN_MONITOR_CONFIG")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return _DEFAULT_CONFIG_PATH


def _resolve_env() -> str | None:
    env = os.environ.get("LOAN_MONITOR_ENV")
    if env:
        return env
    return None


def resolve_secrets_path(config_path: Path, env: str | None) -> Path:
    custom = os.environ.get("LOAN_MONITOR_SECRETS_PATH")
    if custom:
        return Path(custom).expanduser().resolve()
    suffix = config_path.suffix
    stem = config_path.stem
    env_suffix = f".{env}" if env else ""
    filename = f"{stem}{env_suffix}.secrets"
    return config_path.with_name(filename)


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``overlay`` into ``base`` without modifying inputs."""

    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(
    path: Path | None = None,
    *,
    env: str | None = None,
    secrets_passphrase: str | None = None,
    require_secrets: bool = False,
) -> Config:
    """Load YAML configuration file into a :class:`Config` object.

    Parameters
    ----------
    path:
        Optional explicit path to the base configuration file.
    env:
        Logical environment name. Defaults to ``LOAN_MONITOR_ENV``.
    secrets_passphrase:
        Optional passphrase used to decrypt encrypted secrets.
    require_secrets:
        When ``True`` raise if an encrypted secrets file exists but no
        passphrase is supplied. Defaults to ``False`` to aid test harnesses.
    """

    cfg_path = resolve_config_path(path)
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw: Dict[str, Any] = yaml.safe_load(f) or {}

    env_name = env or _resolve_env()
    if env_name:
        env_path = cfg_path.with_name(
            f"{cfg_path.stem}.{env_name}{cfg_path.suffix}"
        )
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as env_file:
                env_raw = yaml.safe_load(env_file) or {}
            raw = _deep_merge(raw, env_raw)

    secrets_path = resolve_secrets_path(cfg_path, env_name)
    manager = SecretManager(
        secrets_path,
        passphrase=secrets_passphrase,
    )
    try:
        secrets_payload = manager.load()
    except SecretPassphraseRequired as exc:
        if require_secrets:
            raise
        raise SecretPassphraseRequired(
            f"{exc}. Provide LOAN_MONITOR_SECRET_PASSPHRASE or call load_config "
            "with secrets_passphrase."
        ) from exc
    else:
        if secrets_payload:
            raw = _deep_merge(raw, secrets_payload)

    thresholds = Thresholds(**raw.get("thresholds", {}))
    terms = Terms(**raw.get("terms", {}))
    security_raw = raw.get("security", {})
    users_raw = security_raw.get("users", {})
    security = SecuritySettings(
        jwt_secret=security_raw.get("jwt_secret", "change-me"),
        jwt_algorithm=security_raw.get("jwt_algorithm", "HS256"),
        token_ttl_seconds=security_raw.get("token_ttl_seconds", 900),
        totp_valid_window=security_raw.get("totp_valid_window", 1),
        users={
            name: SecurityUser(**data)
            for name, data in users_raw.items()
        },
    )
    observability = ObservabilitySettings(**raw.get("observability", {}))
    hedging_raw = raw.get("hedging", {})
    questions_raw = hedging_raw.get("knowledge_check", [])
    questions: list[KnowledgeQuestion] = []
    for item in questions_raw:
        if "id" not in item or "prompt" not in item:
            continue
        answers = item.get("correct_answers")
        if not answers:
            single = item.get("answer")
            answers = [single] if single else []
        answers = [str(ans) for ans in answers if ans is not None]
        if not answers:
            continue
        questions.append(
            KnowledgeQuestion(
                id=str(item["id"]),
                prompt=str(item["prompt"]),
                correct_answers=answers,
            )
        )
    hedging = HedgingSettings(
        enabled=hedging_raw.get("enabled", False),
        require_consent=hedging_raw.get("require_consent", True),
        consent_prompt=hedging_raw.get("consent_prompt", HedgingSettings.consent_prompt),
        consent_version=hedging_raw.get("consent_version", "v1"),
        knowledge_check=questions,
        allowed_strategies=hedging_raw.get("allowed_strategies", HedgingSettings().allowed_strategies),
        paper_trading=hedging_raw.get("paper_trading", True),
        option_quote_url=hedging_raw.get("option_quote_url"),
        futures_quote_url=hedging_raw.get("futures_quote_url"),
        trade_api_url=hedging_raw.get("trade_api_url"),
        default_implied_vol=hedging_raw.get("default_implied_vol", 0.6),
        risk_free_rate=hedging_raw.get("risk_free_rate", 0.02),
        futures_margin_ratio=hedging_raw.get("futures_margin_ratio", 0.1),
        max_notional=hedging_raw.get("max_notional"),
    )

    web_raw = raw.get("web", {})
    allowed_origins = web_raw.get("allowed_origins")
    if allowed_origins is None:
        allowed_origins = WebSettings().allowed_origins
    web = WebSettings(
        host=web_raw.get("host", WebSettings().host),
        port=int(web_raw.get("port", WebSettings().port) or WebSettings().port),
        allowed_origins=[str(origin) for origin in allowed_origins],
    )

    exchanges_raw = raw.get("exchanges", [])
    default_profile_id = str(raw.get("default_profile", "default"))
    exchanges = [
        ExchangeConfig(
            name=item["name"],
            platform=item.get("platform", "unknown"),
            collateral=item.get("collateral", {}),
            loan_outstanding=float(item.get("loan_outstanding", 0.0) or 0.0),
            api_key=item.get("api_key"),
            api_secret=item.get("api_secret"),
            passphrase=item.get("passphrase"),
            safe_withdrawal_ltv=float(item.get("safe_withdrawal_ltv", 0.30) or 0.30),
            warning_ltv=(
                float(item["warning_ltv"]) if item.get("warning_ltv") is not None else None
            ),
            profile=str(item.get("profile", default_profile_id)),
        )
        for item in exchanges_raw
        if "name" in item
    ]

    def _merge_dict(base: dict | None, override: dict | None) -> dict:
        merged = copy.deepcopy(base or {})
        if override:
            for key, value in override.items():
                merged[key] = value
        return merged

    exchanges_by_profile: Dict[str, list[str]] = {}
    for exchange in exchanges:
        exchanges_by_profile.setdefault(exchange.profile, []).append(exchange.name)

    profiles_raw = raw.get("profiles", [])
    profiles: Dict[str, LoanProfileSettings] = {}
    if profiles_raw:
        for item in profiles_raw:
            profile_id = str(item.get("id", "")).strip()
            if not profile_id:
                continue
            profile_thresholds = thresholds
            if "thresholds" in item and item["thresholds"] is not None:
                data = copy.deepcopy(raw.get("thresholds", {}))
                data.update(item.get("thresholds", {}))
                profile_thresholds = Thresholds(**data)
            profile_poll = int(item.get("poll_interval", raw.get("poll_interval", 600)) or 600)
            notification_channels = item.get("notification_channels")
            if notification_channels is None:
                notification_channels = raw.get("notification_channels", [])
            profile_exchanges = item.get("exchanges")
            if profile_exchanges is None:
                profile_exchanges = exchanges_by_profile.get(profile_id, [])
            profiles[profile_id] = LoanProfileSettings(
                id=profile_id,
                name=str(item.get("name", profile_id)),
                loan=_merge_dict(raw.get("loan"), item.get("loan")),
                collateral=_merge_dict(raw.get("collateral"), item.get("collateral")),
                reserves=_merge_dict(raw.get("reserves"), item.get("reserves")),
                policy=_merge_dict(raw.get("policy"), item.get("policy")),
                thresholds=profile_thresholds,
                poll_interval=profile_poll,
                notification_channels=list(notification_channels or []),
                exchanges=list(profile_exchanges or []),
            )
    if not profiles:
        profiles["default"] = LoanProfileSettings(
            id="default",
            name="default",
            loan=_merge_dict({}, raw.get("loan")),
            collateral=_merge_dict({}, raw.get("collateral")),
            reserves=_merge_dict({}, raw.get("reserves")),
            policy=_merge_dict({}, raw.get("policy")),
            thresholds=thresholds,
            poll_interval=int(raw.get("poll_interval", 600) or 600),
            notification_channels=list(raw.get("notification_channels", []) or []),
            exchanges=list(exchanges_by_profile.get("default", []) or [ex.name for ex in exchanges]),
        )
        default_profile_id = "default"
    elif default_profile_id not in profiles:
        default_profile_id = next(iter(profiles))

    for exchange in exchanges:
        if exchange.profile not in profiles:
            exchange.profile = default_profile_id

    default_profile = profiles[default_profile_id]

    return Config(
        api_keys=raw.get("api_keys", {}),
        loan=copy.deepcopy(default_profile.loan),
        collateral=copy.deepcopy(default_profile.collateral),
        reserves=copy.deepcopy(default_profile.reserves),
        policy=copy.deepcopy(default_profile.policy),
        thresholds=thresholds,
        poll_interval=default_profile.poll_interval,
        notification_channels=list(default_profile.notification_channels),
        terms=terms,
        security=security,
        observability=observability,
        hedging=hedging,
        web=web,
        exchanges=exchanges,
        profiles=profiles,
        default_profile=default_profile_id,
    )


__all__ = [
    "Config",
    "Thresholds",
    "Terms",
    "ObservabilitySettings",
    "KnowledgeQuestion",
    "HedgingSettings",
    "WebSettings",
    "ExchangeConfig",
    "LoanProfileSettings",
    "load_config",
]
