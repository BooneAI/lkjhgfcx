from __future__ import annotations

from pathlib import Path

import pytest

from loan_monitor.config import load_config
from loan_monitor.secrets import SecretManager, SecretPassphraseRequired


def test_secret_manager_round_trip(tmp_path: Path) -> None:
    secrets_path = tmp_path / "config.secrets"
    manager = SecretManager(secrets_path, passphrase="topsecret")
    manager.initialise()
    manager.set("api_keys.binance", "abc123")
    manager.set("nested.value", {"foo": "bar"})

    assert manager.get("api_keys.binance") == "abc123"
    assert sorted(manager.list_keys()) == [
        "api_keys",
        "api_keys.binance",
        "nested",
        "nested.value",
        "nested.value.foo",
    ]

    manager.delete("api_keys.binance")
    with pytest.raises(KeyError):
        manager.get("api_keys.binance")


def test_load_config_merges_env_and_secrets(tmp_path: Path) -> None:
    base = tmp_path / "config.yaml"
    base.write_text(
        "api_keys:\n  binance: base\npoll_interval: 30\nthresholds:\n  warning: 0.8\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "config.production.yaml"
    env_file.write_text(
        "poll_interval: 120\nthresholds:\n  warning: 0.75\n",
        encoding="utf-8",
    )
    secrets_path = tmp_path / "config.production.secrets"
    manager = SecretManager(secrets_path, passphrase="topsecret")
    manager.initialise()
    manager.set("api_keys.binance", "secret-value")

    cfg = load_config(path=base, env="production", secrets_passphrase="topsecret")

    assert cfg.poll_interval == 120
    assert cfg.thresholds.warning == 0.75
    assert cfg.api_keys["binance"] == "secret-value"


def test_load_config_requires_passphrase(tmp_path: Path) -> None:
    base = tmp_path / "config.yaml"
    base.write_text("api_keys: {}\n", encoding="utf-8")
    secrets_path = tmp_path / "config.secrets"
    manager = SecretManager(secrets_path, passphrase="secret")
    manager.initialise()
    manager.set("api_keys.binance", "value")

    with pytest.raises(SecretPassphraseRequired):
        load_config(path=base, require_secrets=True)
