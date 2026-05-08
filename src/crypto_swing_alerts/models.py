from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


Provider = Literal["binance_spot", "hyperliquid_perp"]


@dataclass(frozen=True)
class AssetConfig:
    symbol: str
    provider: Provider
    market: str


@dataclass(frozen=True)
class Candle:
    opened_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def timestamp_ms(self) -> int:
        return int(self.opened_at.replace(tzinfo=timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class Signal:
    asset: str
    market: str
    provider: Provider
    score: int
    should_alert: bool
    entry: float
    stop: float
    stop_pct: float
    liquidation_buffer_pct: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk_reward_to_tp2: float
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    generated_at: datetime

    @property
    def risk_per_unit(self) -> float:
        return self.entry - self.stop
