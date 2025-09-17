from __future__ import annotations

import aiohttp
import time
from typing import Optional


class PriceService:
    """Fetch BTC price with caching and failover between sources."""

    def __init__(self, ttl: int = 600) -> None:
        self.ttl = ttl
        self._cache: Optional[float] = None
        self._last_fetch: float = 0.0

    async def _fetch_binance(self) -> float:
        url = "https://api.binance.com/api/v3/ticker/price"
        params = {"symbol": "BTCUSDT"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                data = await resp.json()
        return float(data["price"])

    async def _fetch_coingecko(self) -> float:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "bitcoin", "vs_currencies": "usd"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                data = await resp.json()
        return float(data["bitcoin"]["usd"])

    async def get_price(self) -> float:
        now = time.time()
        if self._cache is not None and (now - self._last_fetch) < self.ttl:
            return self._cache

        for source in (self._fetch_binance, self._fetch_coingecko):
            try:
                price = await source()
                self._cache = price
                self._last_fetch = now
                return price
            except Exception:
                continue
        raise RuntimeError("all price sources failed")

__all__ = ["PriceService"]
