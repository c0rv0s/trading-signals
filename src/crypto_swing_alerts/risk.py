from __future__ import annotations


DEFAULT_MAINTENANCE_MARGIN_PCT = 0.005
DEFAULT_LIQUIDATION_BUFFER_PCT = 0.01


def liquidation_price_long(entry: float, leverage: float, maintenance_margin_pct: float) -> float:
    if entry <= 0 or leverage <= 0:
        return 0.0
    liquidation_drop_pct = max(0.0, (1 / leverage) - maintenance_margin_pct)
    return entry * (1 - liquidation_drop_pct)


def liquidation_buffer_pct(
    entry: float,
    stop: float,
    leverage: float,
    maintenance_margin_pct: float,
) -> float:
    if entry <= 0:
        return 0.0
    liquidation = liquidation_price_long(entry, leverage, maintenance_margin_pct)
    return max(0.0, (stop - liquidation) / entry)


def margin_return_pct(entry: float, exit_price: float, leverage: float) -> float:
    if entry <= 0:
        return 0.0
    return ((exit_price - entry) / entry) * leverage
