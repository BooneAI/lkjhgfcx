"""Encrypted secret storage for sensitive configuration values."""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class SecretError(RuntimeError):
    """Base error for the secret manager."""


class SecretPassphraseRequired(SecretError):
    """Raised when a passphrase is required but missing."""


_SALT_SIZE = 16
_ITERATIONS = 390_000
_MISSING = object()


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a Fernet key from ``passphrase`` and ``salt``."""

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


@dataclass
class SecretManager:
    """Manage encrypted secrets stored alongside configuration files."""

    path: Path
    passphrase: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            self.path = Path(self.path)
        self.path = self.path.expanduser().resolve()
        env_passphrase = os.environ.get("LOAN_MONITOR_SECRET_PASSPHRASE")
        if self.passphrase is None and env_passphrase:
            self.passphrase = env_passphrase

    # -- public API -----------------------------------------------------
    def load(self) -> Dict[str, Any]:
        """Return decrypted secrets as a nested dictionary."""

        if not self.path.exists():
            return {}
        if self.passphrase is None:
            raise SecretPassphraseRequired(
                "Secret passphrase required to decrypt secrets file"
            )
        try:
            payload = json.loads(self.path.read_text("utf-8"))
            salt_b64 = payload["salt"]
            token_str = payload["token"]
        except (json.JSONDecodeError, KeyError) as exc:
            raise SecretError(f"Secrets file {self.path} is corrupted: {exc}") from exc
        try:
            salt = base64.b64decode(salt_b64)
        except (ValueError, TypeError) as exc:
            raise SecretError("Secrets file contains invalid salt") from exc
        token = token_str.encode("utf-8")
        fernet = Fernet(_derive_key(self.passphrase, salt))
        decrypted = fernet.decrypt(token)
        return json.loads(decrypted.decode("utf-8"))

    def list_keys(self) -> Iterable[str]:
        """Yield dotted-path keys contained in the secrets store."""

        data = self.load()
        return list(_flatten_keys(data))

    def get(self, dotted_key: str, default: Any = _MISSING) -> Any:
        """Retrieve a value from the secrets store."""

        data = self.load()
        try:
            return _get_in(data, dotted_key)
        except KeyError:
            if default is _MISSING:
                raise
            return default

    def set(self, dotted_key: str, value: Any) -> None:
        """Create or update a value in the encrypted secrets file."""

        data = self.load()
        if not data and not self.path.exists():
            data = {}
        _set_in(data, dotted_key, value)
        self._write(data)

    def delete(self, dotted_key: str) -> None:
        """Remove a key from the secrets store."""

        data = self.load()
        _delete_in(data, dotted_key)
        self._write(data)

    def initialise(self, *, overwrite: bool = False) -> None:
        """Initialise the secrets file if it doesn't exist."""

        if self.path.exists() and not overwrite:
            raise SecretError("Secrets file already exists; use overwrite=True to replace")
        if self.passphrase is None:
            raise SecretPassphraseRequired(
                "Passphrase required to initialise secrets store"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write({})

    # -- helpers --------------------------------------------------------
    def _write(self, data: Dict[str, Any]) -> None:
        if self.passphrase is None:
            raise SecretPassphraseRequired(
                "Passphrase required to write secrets"
            )
        salt = os.urandom(_SALT_SIZE)
        fernet = Fernet(_derive_key(self.passphrase, salt))
        token = fernet.encrypt(json.dumps(data, sort_keys=True).encode("utf-8"))
        payload = {
            "salt": base64.b64encode(salt).decode("ascii"),
            "token": token.decode("utf-8"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _flatten_keys(data: Dict[str, Any], prefix: str = "") -> Iterable[str]:
    for key, value in data.items():
        dotted = f"{prefix}.{key}" if prefix else key
        yield dotted
        if isinstance(value, dict):
            yield from _flatten_keys(value, dotted)


def _get_in(data: Dict[str, Any], dotted_key: str) -> Any:
    current: Any = data
    for segment in dotted_key.split('.'):
        if not isinstance(current, dict) or segment not in current:
            raise KeyError(dotted_key)
        current = current[segment]
    return current


def _set_in(data: Dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split('.')
    current = data
    for segment in parts[:-1]:
        current = current.setdefault(segment, {})
        if not isinstance(current, dict):
            raise SecretError(
                f"Cannot set {dotted_key}; {segment} is not a mapping"
            )
    current[parts[-1]] = value


def _delete_in(data: Dict[str, Any], dotted_key: str) -> None:
    parts = dotted_key.split('.')
    current = data
    trail = []
    for segment in parts[:-1]:
        if segment not in current or not isinstance(current[segment], dict):
            raise KeyError(dotted_key)
        trail.append((current, segment))
        current = current[segment]
    if parts[-1] not in current:
        raise KeyError(dotted_key)
    del current[parts[-1]]
    for parent, key in reversed(trail):
        child = parent[key]
        if isinstance(child, dict) and not child:
            del parent[key]
        else:
            break

