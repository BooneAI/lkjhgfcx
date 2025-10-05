from __future__ import annotations

import logging


class Notifier:
    async def send(self, level: str, message: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    """Log alerts to the console."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def send(self, level: str, message: str) -> None:
        self.logger.warning("%s: %s", level.upper(), message)


__all__ = ["Notifier", "ConsoleNotifier"]
