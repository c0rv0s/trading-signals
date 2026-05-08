from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime

from .config import Settings, load_settings
from .data import fetch_candles
from .models import AssetConfig, Candle, Signal
from .risk import liquidation_price_long, margin_return_pct
from .strategy import STRATEGIES, analyze_asset


@dataclass(frozen=True)
class BacktestTrade:
    asset: str
    entered_at: datetime
    exited_at: datetime
    entry: float
    exit: float
    stop: float
    target: float
    result_r: float
    outcome: str
    score: int
    leverage: float
    margin_return_pct: float


@dataclass(frozen=True)
class BacktestResult:
    strategy_name: str
    trades: tuple[BacktestTrade, ...]

    @property
    def wins(self) -> int:
        return sum(1 for trade in self.trades if trade.result_r > 0)

    @property
    def losses(self) -> int:
        return sum(1 for trade in self.trades if trade.result_r < 0)

    @property
    def liquidations(self) -> int:
        return sum(1 for trade in self.trades if trade.outcome == "liquidation")

    @property
    def total_r(self) -> float:
        return sum(trade.result_r for trade in self.trades)

    @property
    def win_rate(self) -> float:
        return 0.0 if not self.trades else self.wins / len(self.trades)

    @property
    def average_r(self) -> float:
        return 0.0 if not self.trades else self.total_r / len(self.trades)

    @property
    def profit_factor(self) -> float:
        gains = sum(trade.result_r for trade in self.trades if trade.result_r > 0)
        losses = abs(sum(trade.result_r for trade in self.trades if trade.result_r < 0))
        return gains if losses == 0 else gains / losses

    @property
    def max_drawdown_r(self) -> float:
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for trade in self.trades:
            equity += trade.result_r
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        return max_drawdown

    @property
    def total_margin_return_pct(self) -> float:
        return sum(trade.margin_return_pct for trade in self.trades)

    @property
    def average_margin_return_pct(self) -> float:
        return 0.0 if not self.trades else self.total_margin_return_pct / len(self.trades)


def _daily_slice_for_hour(daily: list[Candle], hour: Candle) -> list[Candle]:
    return [candle for candle in daily if candle.opened_at <= hour.opened_at]


def _simulate_trade(
    signal: Signal,
    future: list[Candle],
    max_hold_hours: int,
    settings: Settings,
) -> BacktestTrade | None:
    risk = signal.entry - signal.stop
    if risk <= 0:
        return None

    target = signal.take_profit_2
    liquidation = liquidation_price_long(signal.entry, settings.leverage, settings.maintenance_margin_pct)
    held = future[:max_hold_hours]
    if not held:
        return None

    for candle in held:
        liquidation_hit = candle.low <= liquidation
        stop_hit = candle.low <= signal.stop
        target_hit = candle.high >= target
        if liquidation_hit:
            return BacktestTrade(
                asset=signal.asset,
                entered_at=signal.generated_at,
                exited_at=candle.opened_at,
                entry=signal.entry,
                exit=liquidation,
                stop=signal.stop,
                target=target,
                result_r=(liquidation - signal.entry) / risk,
                outcome="liquidation",
                score=signal.score,
                leverage=settings.leverage,
                margin_return_pct=-1.0,
            )
        if stop_hit:
            return BacktestTrade(
                asset=signal.asset,
                entered_at=signal.generated_at,
                exited_at=candle.opened_at,
                entry=signal.entry,
                exit=signal.stop,
                stop=signal.stop,
                target=target,
                result_r=-1.0,
                outcome="stop",
                score=signal.score,
                leverage=settings.leverage,
                margin_return_pct=margin_return_pct(signal.entry, signal.stop, settings.leverage),
            )
        if target_hit:
            result_r = (target - signal.entry) / risk
            return BacktestTrade(
                asset=signal.asset,
                entered_at=signal.generated_at,
                exited_at=candle.opened_at,
                entry=signal.entry,
                exit=target,
                stop=signal.stop,
                target=target,
                result_r=result_r,
                outcome="tp2",
                score=signal.score,
                leverage=settings.leverage,
                margin_return_pct=margin_return_pct(signal.entry, target, settings.leverage),
            )

    final = held[-1]
    return BacktestTrade(
        asset=signal.asset,
        entered_at=signal.generated_at,
        exited_at=final.opened_at,
        entry=signal.entry,
        exit=final.close,
        stop=signal.stop,
        target=target,
        result_r=(final.close - signal.entry) / risk,
        outcome="timeout",
        score=signal.score,
        leverage=settings.leverage,
        margin_return_pct=margin_return_pct(signal.entry, final.close, settings.leverage),
    )


def backtest_asset(
    asset: AssetConfig,
    daily: list[Candle],
    hourly: list[Candle],
    settings: Settings,
    max_hold_hours: int = 72,
) -> BacktestResult:
    trades: list[BacktestTrade] = []
    next_entry_index = 120

    for index in range(120, len(hourly) - 1):
        if index < next_entry_index:
            continue

        hourly_window = hourly[: index + 1]
        daily_window = _daily_slice_for_hour(daily, hourly[index])
        signal = analyze_asset(asset, daily_window, hourly_window, settings)
        if not signal.should_alert:
            continue

        signal = Signal(
            asset=signal.asset,
            market=signal.market,
            provider=signal.provider,
            score=signal.score,
            should_alert=signal.should_alert,
            entry=signal.entry,
            stop=signal.stop,
            stop_pct=signal.stop_pct,
            liquidation_buffer_pct=signal.liquidation_buffer_pct,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            take_profit_3=signal.take_profit_3,
            risk_reward_to_tp2=signal.risk_reward_to_tp2,
            reasons=signal.reasons,
            blockers=signal.blockers,
            generated_at=hourly[index].opened_at,
        )
        trade = _simulate_trade(signal, hourly[index + 1 :], max_hold_hours, settings)
        if trade is None:
            continue
        trades.append(trade)
        next_entry_index = index + max_hold_hours

    return BacktestResult(settings.strategy_name, tuple(trades))


def combine_results(strategy_name: str, results: list[BacktestResult]) -> BacktestResult:
    trades = tuple(sorted((trade for result in results for trade in result.trades), key=lambda trade: trade.entered_at))
    return BacktestResult(strategy_name, trades)


def _print_result(asset: AssetConfig, result: BacktestResult) -> None:
    print(f"{asset.symbol} {result.strategy_name}")
    print(
        f"trades={len(result.trades)} wins={result.wins} losses={result.losses} liq={result.liquidations} "
        f"win_rate={result.win_rate:.1%} total_r={result.total_r:.2f} "
        f"avg_r={result.average_r:.2f} pf={result.profit_factor:.2f} max_dd_r={result.max_drawdown_r:.2f} "
        f"margin_total={result.total_margin_return_pct:.1%} margin_avg={result.average_margin_return_pct:.1%}"
    )
    for trade in result.trades[-10:]:
        print(
            f"{trade.entered_at.isoformat()} {trade.outcome} "
            f"entry={trade.entry:.6g} exit={trade.exit:.6g} r={trade.result_r:.2f} "
            f"margin={trade.margin_return_pct:.1%} score={trade.score}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest a registered crypto swing strategy.")
    parser.add_argument("--strategy", default=None, help="Strategy name, or 'all' to compare every registered strategy.")
    parser.add_argument("--max-hold-hours", type=int, default=72)
    parser.add_argument("--daily-lookback-days", type=int)
    parser.add_argument("--hourly-lookback-hours", type=int)
    parser.add_argument("--leverage", type=float)
    parser.add_argument("--leverage-sweep", action="store_true", help="Run each strategy at 3x through 10x.")
    args = parser.parse_args()

    settings = load_settings()
    if args.daily_lookback_days is not None:
        settings = Settings(**{**settings.__dict__, "daily_lookback_days": args.daily_lookback_days})
    if args.hourly_lookback_hours is not None:
        settings = Settings(**{**settings.__dict__, "hourly_lookback_hours": args.hourly_lookback_hours})
    if args.leverage is not None:
        settings = Settings(**{**settings.__dict__, "leverage": args.leverage})

    strategy_names = sorted(STRATEGIES) if args.strategy == "all" else (args.strategy or settings.strategy_name,)
    candles_by_asset: dict[str, tuple[list[Candle], list[Candle]]] = {}
    for asset in settings.assets:
        daily = fetch_candles(asset, "1d", settings.daily_lookback_days)
        hourly = fetch_candles(asset, "1h", settings.hourly_lookback_hours)
        candles_by_asset[asset.symbol] = (daily, hourly)

    leverages = range(3, 11) if args.leverage_sweep else (settings.leverage,)
    for leverage in leverages:
        print(f"=== leverage={float(leverage):.1f}x ===")
        for strategy_name in strategy_names:
            strategy_settings = Settings(**{**settings.__dict__, "strategy_name": strategy_name, "leverage": float(leverage)})
            strategy_results: list[BacktestResult] = []
            for asset in settings.assets:
                daily, hourly = candles_by_asset[asset.symbol]
                result = backtest_asset(asset, daily, hourly, strategy_settings, max_hold_hours=args.max_hold_hours)
                strategy_results.append(result)
                if not args.leverage_sweep:
                    _print_result(asset, result)
            combined = combine_results(strategy_name, strategy_results)
            print(f"ALL {strategy_name}")
            print(
                f"trades={len(combined.trades)} wins={combined.wins} losses={combined.losses} "
                f"liq={combined.liquidations} win_rate={combined.win_rate:.1%} total_r={combined.total_r:.2f} "
                f"avg_r={combined.average_r:.2f} pf={combined.profit_factor:.2f} "
                f"max_dd_r={combined.max_drawdown_r:.2f} margin_total={combined.total_margin_return_pct:.1%} "
                f"margin_avg={combined.average_margin_return_pct:.1%}"
            )


if __name__ == "__main__":
    main()
