from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyotp
import pytest
from fastapi.testclient import TestClient

from loan_monitor.config import (
    Config,
    HedgingSettings,
    LoanProfileSettings,
    ObservabilitySettings,
    Thresholds,
    Terms,
    WebSettings,
)
from loan_monitor.db import get_connection
from loan_monitor.security import SecuritySettings, SecurityUser
from loan_monitor.web import create_app


class DummyPriceService:
    async def get_price(self) -> float:  # pragma: no cover - tiny shim
        return 20_000.0


def make_config(secret: str) -> Config:
    thresholds = Thresholds()
    profile = LoanProfileSettings(
        id="default",
        name="default",
        loan={"principal": 1000.0, "interest": 10.0},
        collateral={"btc": 0.5, "usdt": 300.0},
        reserves={"btc": 0.2, "usdt": 500.0},
        policy={},
        thresholds=thresholds,
        poll_interval=600,
        notification_channels=[],
        exchanges=[],
    )
    security = SecuritySettings(
        jwt_secret="unit-test-secret",
        users={"alice": SecurityUser(role="admin", totp_secret=secret)},
    )
    return Config(
        api_keys={},
        loan=profile.loan,
        collateral=profile.collateral,
        thresholds=thresholds,
        poll_interval=profile.poll_interval,
        notification_channels=[],
        reserves=profile.reserves,
        policy=profile.policy,
        terms=Terms(),
        security=security,
        observability=ObservabilitySettings(),
        hedging=HedgingSettings(),
        exchanges=[],
        profiles={"default": profile},
        default_profile="default",
        web=WebSettings(allowed_origins=["*"]),
    )


@pytest.fixture()
def api_client(tmp_path: Path):
    secret = pyotp.random_base32()
    cfg = make_config(secret)
    db_path = tmp_path / "api.db"
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO loan(profile, principal, interest) VALUES(?, ?, ?)",
        ("default", 1000.0, 10.0),
    )
    conn.executemany(
        "INSERT INTO reserves(profile, asset, pledged, unpledged) VALUES(?, ?, ?, ?)",
        [
            ("default", "btc", 0.1, 0.1),
            ("default", "usdt", 200.0, 500.0),
        ],
    )
    now = datetime.utcnow()
    for idx in range(3):
        conn.execute(
            """
            INSERT INTO ltv_history(
                profile, ltv, btc_amount, usdt_amount, btc_price,
                principal, interest, alert_level, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "default",
                0.70 + idx * 0.02,
                0.1,
                200.0,
                19_500.0,
                1000.0,
                10.0,
                "warning" if idx else "none",
                (now - timedelta(hours=3 - idx)).isoformat(),
            ),
        )
    conn.commit()
    conn.close()
    app = create_app(cfg, db_path=db_path, price_service=DummyPriceService())
    client = TestClient(app)
    token = pyotp.TOTP(secret).now()
    auth_response = client.post("/auth/token", json={"username": "alice", "totp": token})
    assert auth_response.status_code == 200
    payload = auth_response.json()
    headers = {"Authorization": f"Bearer {payload['token']}"}
    return client, headers


def test_profiles_and_snapshot(api_client):
    client, headers = api_client
    profiles = client.get("/api/profiles", headers=headers)
    assert profiles.status_code == 200
    data = profiles.json()
    assert data["default"] == "default"
    assert data["profiles"][0]["thresholds"]["warning"] == 0.8

    snapshot = client.get("/api/dashboard", headers=headers)
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["ltv"] > 0
    assert payload["status"] in {"safe", "warning", "margin_call", "liquidation_risk"}


def test_history_and_actions(api_client):
    client, headers = api_client
    history = client.get("/api/history", headers=headers)
    assert history.status_code == 200
    entries = history.json()["entries"]
    assert len(entries) == 3

    summary = client.get("/api/history/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["count"] == 3

    transfer = client.post(
        "/api/actions/reserves/transfer",
        headers=headers,
        json={"asset": "btc", "amount": 0.05, "direction": "to_collateral"},
    )
    assert transfer.status_code == 200
    balances = transfer.json()["balances"]
    assert pytest.approx(balances["btc"]["pledged"], rel=1e-6) == 0.15

    repay = client.post(
        "/api/actions/repay",
        headers=headers,
        json={"amount": 100.0},
    )
    assert repay.status_code == 200
    result = repay.json()
    assert pytest.approx(result["principal"], rel=1e-6) == 900.0
