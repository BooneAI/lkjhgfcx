"""JWT and TOTP authentication helpers for securing write operations."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict

import jwt
import pyotp


ROLE_ORDER = ["viewer", "trader", "admin"]


@dataclass
class SecurityUser:
    role: str
    totp_secret: str
    disabled: bool = False


@dataclass
class SecuritySettings:
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    token_ttl_seconds: int = 900
    totp_valid_window: int = 1
    users: Dict[str, SecurityUser] = field(default_factory=dict)

    def get_user(self, username: str) -> SecurityUser:
        try:
            return self.users[username]
        except KeyError as exc:  # pragma: no cover - defensive
            raise ValueError("unknown user") from exc


class AuthManager:
    """Authenticate users with TOTP and issue JWT tokens."""

    def __init__(self, settings: SecuritySettings) -> None:
        self.settings = settings

    def authenticate(self, username: str, totp_code: str) -> str:
        user = self.settings.get_user(username)
        if user.disabled:
            raise ValueError("user disabled")
        totp = pyotp.TOTP(user.totp_secret)
        if not totp.verify(totp_code, valid_window=self.settings.totp_valid_window):
            raise ValueError("invalid TOTP")
        now = dt.datetime.utcnow()
        payload = {
            "sub": username,
            "role": user.role,
            "iat": now,
            "exp": now + dt.timedelta(seconds=self.settings.token_ttl_seconds),
        }
        token = jwt.encode(payload, self.settings.jwt_secret, algorithm=self.settings.jwt_algorithm)
        # PyJWT>=2 returns string, but type checker expects str
        return token if isinstance(token, str) else token.decode("utf-8")

    def verify_token(self, token: str, required_role: str | None = None) -> dict:
        try:
            payload = jwt.decode(
                token,
                self.settings.jwt_secret,
                algorithms=[self.settings.jwt_algorithm],
            )
        except jwt.PyJWTError as exc:  # pragma: no cover - library error mapping
            raise ValueError("invalid token") from exc
        role = payload.get("role")
        if required_role and not self._has_role(role, required_role):
            raise PermissionError("insufficient role")
        return payload

    def _has_role(self, current: str | None, required: str) -> bool:
        try:
            current_idx = ROLE_ORDER.index(current or "")
        except ValueError:
            return False
        return current_idx >= ROLE_ORDER.index(required)


__all__ = ["AuthManager", "SecuritySettings", "SecurityUser", "ROLE_ORDER"]
