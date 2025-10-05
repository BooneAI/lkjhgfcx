import time

import pyotp
import pytest

from loan_monitor.security import AuthManager, SecuritySettings, SecurityUser


def test_auth_manager_generates_and_validates_token():
    secret = pyotp.random_base32()
    settings = SecuritySettings(
        jwt_secret="test-secret",
        token_ttl_seconds=30,
        users={"alice": SecurityUser(role="admin", totp_secret=secret)},
    )
    manager = AuthManager(settings)
    totp = pyotp.TOTP(secret)
    token = manager.authenticate("alice", totp.now())
    payload = manager.verify_token(token, required_role="trader")
    assert payload["sub"] == "alice"
    assert payload["role"] == "admin"


def test_auth_manager_rejects_invalid_totp():
    secret = pyotp.random_base32()
    settings = SecuritySettings(
        jwt_secret="test-secret",
        users={"bob": SecurityUser(role="trader", totp_secret=secret)},
    )
    manager = AuthManager(settings)
    with pytest.raises(ValueError):
        manager.authenticate("bob", "000000")


def test_auth_manager_enforces_role_hierarchy():
    secret = pyotp.random_base32()
    settings = SecuritySettings(
        jwt_secret="test-secret",
        users={"carol": SecurityUser(role="viewer", totp_secret=secret)},
    )
    manager = AuthManager(settings)
    token = manager.authenticate("carol", pyotp.TOTP(secret).now())
    with pytest.raises(PermissionError):
        manager.verify_token(token, required_role="trader")


def test_expired_token_is_rejected(monkeypatch):
    secret = pyotp.random_base32()
    settings = SecuritySettings(
        jwt_secret="test-secret",
        token_ttl_seconds=1,
        users={"dave": SecurityUser(role="trader", totp_secret=secret)},
    )
    manager = AuthManager(settings)
    token = manager.authenticate("dave", pyotp.TOTP(secret).now())
    time.sleep(2)
    with pytest.raises(ValueError):
        manager.verify_token(token)

