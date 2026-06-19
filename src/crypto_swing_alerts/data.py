from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import AssetConfig, Candle


BINANCE_BASE_URL = "https://api.binance.com"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
HYPERLIQUID_FALLBACK_SYMBOLS = {"BTC", "ETH", "HYPE", "NEAR", "PENGU", "SOL", "WLD", "XMR", "XRP", "ZEC"}
TIMEOUT_SECONDS = 20
LOGGER = logging.getLogger("crypto_swing_alerts.data")


def _utc_from_ms(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def _get_json(url: str, params: dict[str, Any]) -> Any:
    query = urlencode(params)
    request = Request(
        f"{url}?{query}",
        headers={"User-Agent": "crypto-swing-alerts/0.1"},
        method="GET",
    )
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "crypto-swing-alerts/0.1"},
        method="POST",
    )
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _interval_ms(interval: str) -> int:
    if interval == "1h":
        return 60 * 60 * 1000
    if interval == "1d":
        return 24 * 60 * 60 * 1000
    raise ValueError(f"Unsupported interval: {interval}")


def _drop_incomplete(candles: list[Candle], interval: str) -> list[Candle]:
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    candle_ms = _interval_ms(interval)
    return [candle for candle in candles if candle.timestamp_ms + candle_ms <= now_ms]


def _parse_binance_klines(raw: list[Any]) -> list[Candle]:
    return [
        Candle(
            opened_at=_utc_from_ms(int(row[0])),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in raw
    ]


def _fetch_binance_klines(market: str, interval: str, limit: int) -> list[Candle]:
    raw_rows: list[Any] = []
    end_time = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    remaining = limit + 5
    while remaining > 0:
        batch = _get_json(
            f"{BINANCE_BASE_URL}/api/v3/klines",
            {
                "symbol": market.upper(),
                "interval": interval,
                "limit": min(remaining, 1000),
                "endTime": end_time,
            },
        )
        if not batch:
            break
        raw_rows = batch + raw_rows
        first_open_time = int(batch[0][0])
        end_time = first_open_time - 1
        remaining -= len(batch)
        if len(batch) < 1000:
            break

    seen: set[int] = set()
    candles = [
        candle
        for candle in _parse_binance_klines(raw_rows)
        if candle.timestamp_ms not in seen and not seen.add(candle.timestamp_ms)
    ]
    candles.sort(key=lambda candle: candle.opened_at)
    return _drop_incomplete(candles[-limit:], interval)


def _fetch_hyperliquid_candles(market: str, interval: str, lookback: int) -> list[Candle]:
    interval_millis = _interval_ms(interval)
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(milliseconds=interval_millis * min(lookback + 5, 5000))
    raw = _post_json(
        HYPERLIQUID_INFO_URL,
        {
            "type": "candleSnapshot",
            "req": {
                "coin": market.upper(),
                "interval": interval,
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
            },
        },
    )
    candles = [
        Candle(
            opened_at=_utc_from_ms(int(row.get("t", row["T"]))),
            open=float(row["o"]),
            high=float(row["h"]),
            low=float(row["l"]),
            close=float(row["c"]),
            volume=float(row["v"]),
        )
        for row in raw
    ]
    candles.sort(key=lambda candle: candle.opened_at)
    return _drop_incomplete(candles[-lookback:], interval)


def fetch_candles(asset: AssetConfig, interval: str, lookback: int) -> list[Candle]:
    if asset.provider == "binance_spot":
        try:
            return _fetch_binance_klines(asset.market, interval, lookback)
        except HTTPError as error:
            if error.code != 451 or asset.symbol not in HYPERLIQUID_FALLBACK_SYMBOLS:
                raise
            LOGGER.warning(
                "Binance returned HTTP 451 for %s/%s; falling back to Hyperliquid candles.",
                asset.symbol,
                asset.market,
            )
            return _fetch_hyperliquid_candles(asset.symbol, interval, lookback)
    if asset.provider == "hyperliquid_perp":
        return _fetch_hyperliquid_candles(asset.market, interval, lookback)
    raise ValueError(f"Unsupported provider: {asset.provider}")
