from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings, load_settings
from .data import fetch_candles
from .indicators import atr, ema
from .models import AssetConfig, Candle, Signal
from .risk import liquidation_price_long, margin_return_pct
from .strategy import STRATEGIES, analyze_asset, effective_strategy_name, take_profit_r_levels_for_score


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


@dataclass(frozen=True)
class SignalSetup:
    signal: Signal
    entry_index: int


@dataclass(frozen=True)
class ExitPolicySummary:
    asset: str
    strategy_name: str
    leverage: float
    policy: str
    trades: int
    wins: int
    losses: int
    liquidations: int
    win_rate: float
    total_r: float
    average_r: float
    profit_factor: float
    max_drawdown_r: float
    total_margin_return_pct: float
    average_margin_return_pct: float
    average_mfe_r: float
    median_mfe_r: float
    p75_mfe_r: float
    p90_mfe_r: float
    hit_3r_rate: float
    hit_5r_rate: float
    hit_8r_rate: float
    hit_10r_rate: float


def _daily_slice_for_hour(daily: list[Candle], hour: Candle) -> list[Candle]:
    return [candle for candle in daily if candle.opened_at <= hour.opened_at]


def _profit_factor(values: list[float]) -> float:
    gains = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    return gains if losses == 0 else gains / losses


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return max_drawdown


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _target_r(signal: Signal, target: float) -> float:
    risk = signal.entry - signal.stop
    return 0.0 if risk <= 0 else (target - signal.entry) / risk


def _mfe_r_before_initial_stop(signal: Signal, future: list[Candle], max_hold_hours: int, settings: Settings) -> float:
    risk = signal.entry - signal.stop
    if risk <= 0:
        return 0.0
    liquidation = liquidation_price_long(signal.entry, settings.leverage, settings.maintenance_margin_pct)
    max_r = 0.0
    for candle in future[:max_hold_hours]:
        if candle.low <= liquidation or candle.low <= signal.stop:
            return max_r
        max_r = max(max_r, (candle.high - signal.entry) / risk)
    return max_r


def _collect_setups(
    asset: AssetConfig,
    daily: list[Candle],
    hourly: list[Candle],
    settings: Settings,
    max_hold_hours: int,
) -> list[SignalSetup]:
    setups: list[SignalSetup] = []
    next_entry_index = 120
    for index in range(120, len(hourly) - 1):
        if index < next_entry_index:
            continue
        hourly_window = hourly[: index + 1]
        daily_window = _daily_slice_for_hour(daily, hourly[index])
        signal = analyze_asset(asset, daily_window, hourly_window, settings)
        if not signal.should_alert:
            continue
        setups.append(
            SignalSetup(
                signal=Signal(
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
                ),
                entry_index=index,
            )
        )
        next_entry_index = index + max_hold_hours
    return setups


def _simulate_trade(
    signal: Signal,
    future: list[Candle],
    max_hold_hours: int,
    settings: Settings,
) -> BacktestTrade | None:
    return _simulate_ladder(
        signal,
        future,
        max_hold_hours,
        settings,
        (
            _target_r(signal, signal.take_profit_1),
            _target_r(signal, signal.take_profit_2),
            _target_r(signal, signal.take_profit_3),
        ),
    )


def _simulate_fixed_target(
    signal: Signal,
    future: list[Candle],
    max_hold_hours: int,
    settings: Settings,
    target_r: float,
) -> BacktestTrade | None:
    risk = signal.entry - signal.stop
    if risk <= 0:
        return None
    target = signal.entry + target_r * risk
    liquidation = liquidation_price_long(signal.entry, settings.leverage, settings.maintenance_margin_pct)
    held = future[:max_hold_hours]
    if not held:
        return None

    for candle in held:
        if candle.low <= liquidation:
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
        if candle.low <= signal.stop:
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
        if candle.high >= target:
            return BacktestTrade(
                asset=signal.asset,
                entered_at=signal.generated_at,
                exited_at=candle.opened_at,
                entry=signal.entry,
                exit=target,
                stop=signal.stop,
                target=target,
                result_r=target_r,
                outcome=f"tp{target_r:g}r",
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


def _simulate_ladder(
    signal: Signal,
    future: list[Candle],
    max_hold_hours: int,
    settings: Settings,
    target_r_levels: tuple[float, float, float],
) -> BacktestTrade | None:
    risk = signal.entry - signal.stop
    if risk <= 0:
        return None
    liquidation = liquidation_price_long(signal.entry, settings.leverage, settings.maintenance_margin_pct)
    held = future[:max_hold_hours]
    if not held:
        return None

    remaining = 1.0
    realized_r = 0.0
    hit_targets = [False, False, False]
    exit_price = held[-1].close
    exited_at = held[-1].opened_at
    outcome = "timeout"

    for candle in held:
        if candle.low <= liquidation:
            realized_r += remaining * ((liquidation - signal.entry) / risk)
            exit_price = liquidation
            exited_at = candle.opened_at
            outcome = "liquidation"
            remaining = 0.0
            break
        if candle.low <= signal.stop:
            realized_r -= remaining
            exit_price = signal.stop
            exited_at = candle.opened_at
            outcome = "stop" if not any(hit_targets) else "partial_stop"
            remaining = 0.0
            break

        for index, target_r in enumerate(target_r_levels):
            target = signal.entry + target_r * risk
            if not hit_targets[index] and candle.high >= target:
                realized_r += (1.0 / 3.0) * target_r
                remaining -= 1.0 / 3.0
                hit_targets[index] = True
                exit_price = target
                exited_at = candle.opened_at
                outcome = f"tp{index + 1}"

        if remaining <= 0.0001:
            outcome = "tp_ladder"
            remaining = 0.0
            break

    if remaining > 0:
        final = held[-1]
        exit_r = (final.close - signal.entry) / risk
        realized_r += remaining * exit_r
        exit_price = final.close
        exited_at = final.opened_at

    return BacktestTrade(
        asset=signal.asset,
        entered_at=signal.generated_at,
        exited_at=exited_at,
        entry=signal.entry,
        exit=exit_price,
        stop=signal.stop,
        target=signal.entry + target_r_levels[-1] * risk,
        result_r=realized_r,
        outcome=outcome,
        score=signal.score,
        leverage=settings.leverage,
        margin_return_pct=realized_r * (risk / signal.entry) * settings.leverage,
    )


def _simulate_scaled_runner(
    signal: Signal,
    future: list[Candle],
    future_ema: list[float | None],
    future_atr: list[float | None],
    max_hold_hours: int,
    settings: Settings,
    trail: str,
) -> BacktestTrade | None:
    risk = signal.entry - signal.stop
    if risk <= 0:
        return None
    liquidation = liquidation_price_long(signal.entry, settings.leverage, settings.maintenance_margin_pct)
    held = future[:max_hold_hours]
    if not held:
        return None

    remaining = 1.0
    realized_r = 0.0
    active_stop = signal.stop
    hit_tp1 = False
    hit_tp2 = False
    peak_close = signal.entry
    exit_price = held[-1].close
    exited_at = held[-1].opened_at
    outcome = "timeout"

    for offset, candle in enumerate(held):
        peak_close = max(peak_close, candle.close)
        if candle.low <= liquidation:
            realized_r += remaining * ((liquidation - signal.entry) / risk)
            exit_price = liquidation
            exited_at = candle.opened_at
            outcome = "liquidation"
            remaining = 0.0
            break
        if candle.low <= active_stop:
            realized_r += remaining * ((active_stop - signal.entry) / risk)
            exit_price = active_stop
            exited_at = candle.opened_at
            outcome = "stop"
            remaining = 0.0
            break

        if not hit_tp1 and candle.high >= signal.entry + 1.5 * risk:
            realized_r += 0.25 * 1.5
            remaining -= 0.25
            active_stop = max(active_stop, signal.entry)
            hit_tp1 = True
        if not hit_tp2 and candle.high >= signal.entry + 3.0 * risk:
            realized_r += 0.25 * 3.0
            remaining -= 0.25
            hit_tp2 = True

        if hit_tp2 and remaining > 0:
            trailing_stop = active_stop
            if trail == "ema21":
                trailing_stop = max(trailing_stop, future_ema[offset] or trailing_stop)
            elif trail == "ema55":
                trailing_stop = max(trailing_stop, future_ema[offset] or trailing_stop)
            elif trail == "atr2":
                atr_value = future_atr[offset]
                if atr_value is not None:
                    trailing_stop = max(trailing_stop, peak_close - 2 * atr_value)
            active_stop = trailing_stop
            if candle.close <= active_stop:
                runner_r = (candle.close - signal.entry) / risk
                realized_r += remaining * runner_r
                exit_price = candle.close
                exited_at = candle.opened_at
                outcome = f"trail_{trail}"
                remaining = 0.0
                break

    if remaining > 0:
        final = held[-1]
        realized_r += remaining * ((final.close - signal.entry) / risk)
        exit_price = final.close
        exited_at = final.opened_at

    return BacktestTrade(
        asset=signal.asset,
        entered_at=signal.generated_at,
        exited_at=exited_at,
        entry=signal.entry,
        exit=exit_price,
        stop=signal.stop,
        target=signal.entry + 3 * risk,
        result_r=realized_r,
        outcome=outcome,
        score=signal.score,
        leverage=settings.leverage,
        margin_return_pct=realized_r * (risk / signal.entry) * settings.leverage,
    )


def _summarize_policy(
    asset: str,
    strategy_name: str,
    leverage: float,
    policy: str,
    trades: list[BacktestTrade],
    mfe_values: list[float],
) -> ExitPolicySummary:
    result_values = [trade.result_r for trade in trades]
    margin_values = [trade.margin_return_pct for trade in trades]
    wins = sum(1 for value in result_values if value > 0)
    losses = sum(1 for value in result_values if value < 0)
    total_r = sum(result_values)
    return ExitPolicySummary(
        asset=asset,
        strategy_name=strategy_name,
        leverage=leverage,
        policy=policy,
        trades=len(trades),
        wins=wins,
        losses=losses,
        liquidations=sum(1 for trade in trades if trade.outcome == "liquidation"),
        win_rate=0.0 if not trades else wins / len(trades),
        total_r=total_r,
        average_r=0.0 if not trades else total_r / len(trades),
        profit_factor=_profit_factor(result_values),
        max_drawdown_r=_max_drawdown(result_values),
        total_margin_return_pct=sum(margin_values),
        average_margin_return_pct=0.0 if not trades else sum(margin_values) / len(trades),
        average_mfe_r=0.0 if not mfe_values else sum(mfe_values) / len(mfe_values),
        median_mfe_r=_percentile(mfe_values, 0.5),
        p75_mfe_r=_percentile(mfe_values, 0.75),
        p90_mfe_r=_percentile(mfe_values, 0.9),
        hit_3r_rate=0.0 if not mfe_values else sum(1 for value in mfe_values if value >= 3) / len(mfe_values),
        hit_5r_rate=0.0 if not mfe_values else sum(1 for value in mfe_values if value >= 5) / len(mfe_values),
        hit_8r_rate=0.0 if not mfe_values else sum(1 for value in mfe_values if value >= 8) / len(mfe_values),
        hit_10r_rate=0.0 if not mfe_values else sum(1 for value in mfe_values if value >= 10) / len(mfe_values),
    )


def analyze_exit_policies_asset(
    asset: AssetConfig,
    daily: list[Candle],
    hourly: list[Candle],
    settings: Settings,
    max_hold_hours: int = 168,
) -> list[ExitPolicySummary]:
    setups = _collect_setups(asset, daily, hourly, settings, max_hold_hours)
    hourly_closes = [candle.close for candle in hourly]
    ema21_values = ema(hourly_closes, 21)
    ema55_values = ema(hourly_closes, 55)
    atr14_values = atr(hourly, 14)
    policies = (
        "static_ladder",
        "score_ladder",
        "fixed_3r",
        "fixed_5r",
        "fixed_8r",
        "fixed_10r",
        "runner_ema21",
        "runner_ema55",
        "runner_atr2",
    )
    policy_trades: dict[str, list[BacktestTrade]] = {policy: [] for policy in policies}
    mfe_values: list[float] = []

    for setup in setups:
        future = hourly[setup.entry_index + 1 :]
        mfe_values.append(_mfe_r_before_initial_stop(setup.signal, future, max_hold_hours, settings))
        for target_r in (3.0, 5.0, 8.0, 10.0):
            trade = _simulate_fixed_target(setup.signal, future, max_hold_hours, settings, target_r)
            if trade is not None:
                policy_trades[f"fixed_{target_r:g}r"].append(trade)
        trade = _simulate_ladder(setup.signal, future, max_hold_hours, settings, (1.5, 3.0, 5.0))
        if trade is not None:
            policy_trades["static_ladder"].append(trade)
        trade = _simulate_ladder(
            setup.signal,
            future,
            max_hold_hours,
            settings,
            take_profit_r_levels_for_score(setup.signal.score),
        )
        if trade is not None:
            policy_trades["score_ladder"].append(trade)
        for trail, ema_values in (("ema21", ema21_values), ("ema55", ema55_values)):
            future_ema = ema_values[setup.entry_index + 1 : setup.entry_index + 1 + max_hold_hours]
            future_atr = atr14_values[setup.entry_index + 1 : setup.entry_index + 1 + max_hold_hours]
            trade = _simulate_scaled_runner(setup.signal, future, future_ema, future_atr, max_hold_hours, settings, trail)
            if trade is not None:
                policy_trades[f"runner_{trail}"].append(trade)
        future_ema = ema21_values[setup.entry_index + 1 : setup.entry_index + 1 + max_hold_hours]
        future_atr = atr14_values[setup.entry_index + 1 : setup.entry_index + 1 + max_hold_hours]
        trade = _simulate_scaled_runner(setup.signal, future, future_ema, future_atr, max_hold_hours, settings, "atr2")
        if trade is not None:
            policy_trades["runner_atr2"].append(trade)

    return [
        _summarize_policy(asset.symbol, effective_strategy_name(asset, settings), settings.leverage, policy, trades, mfe_values)
        for policy, trades in policy_trades.items()
    ]


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

    return BacktestResult(effective_strategy_name(asset, settings), tuple(trades))


def combine_results(strategy_name: str, results: list[BacktestResult]) -> BacktestResult:
    trades = tuple(sorted((trade for result in results for trade in result.trades), key=lambda trade: trade.entered_at))
    return BacktestResult(strategy_name, trades)


def _without_asset_strategy(asset: AssetConfig) -> AssetConfig:
    return AssetConfig(symbol=asset.symbol, provider=asset.provider, market=asset.market)


@dataclass(frozen=True)
class BacktestSummary:
    asset: str
    strategy_name: str
    leverage: float
    trades: int
    wins: int
    losses: int
    liquidations: int
    win_rate: float
    total_r: float
    average_r: float
    profit_factor: float
    max_drawdown_r: float
    total_margin_return_pct: float
    average_margin_return_pct: float


def _summary(asset: str, strategy_name: str, leverage: float, result: BacktestResult) -> BacktestSummary:
    return BacktestSummary(
        asset=asset,
        strategy_name=strategy_name,
        leverage=leverage,
        trades=len(result.trades),
        wins=result.wins,
        losses=result.losses,
        liquidations=result.liquidations,
        win_rate=result.win_rate,
        total_r=result.total_r,
        average_r=result.average_r,
        profit_factor=result.profit_factor,
        max_drawdown_r=result.max_drawdown_r,
        total_margin_return_pct=result.total_margin_return_pct,
        average_margin_return_pct=result.average_margin_return_pct,
    )


def _write_reports(
    report_dir: Path,
    report_name: str,
    settings: Settings,
    strategy_names: tuple[str, ...],
    leverages: tuple[float, ...],
    max_hold_hours: int,
    summaries: list[BacktestSummary],
    trades: list[BacktestTrade],
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / f"{report_name}.csv"
    md_path = report_dir / f"{report_name}.md"

    with csv_path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(BacktestSummary.__dataclass_fields__))
        writer.writeheader()
        for row in summaries:
            writer.writerow({field: getattr(row, field) for field in writer.fieldnames})

    generated_at = datetime.now(tz=timezone.utc).isoformat()
    with md_path.open("w") as md_file:
        md_file.write(f"# Backtest Report: {report_name}\n\n")
        md_file.write(f"- Generated: `{generated_at}`\n")
        md_file.write(f"- Watchlist: `{','.join(asset.symbol for asset in settings.assets)}`\n")
        md_file.write(f"- Strategies: `{','.join(strategy_names)}`\n")
        md_file.write(f"- Leverages: `{','.join(f'{leverage:g}x' for leverage in leverages)}`\n")
        md_file.write(f"- Daily lookback: `{settings.daily_lookback_days}` candles\n")
        md_file.write(f"- Hourly lookback: `{settings.hourly_lookback_hours}` candles\n")
        md_file.write(f"- Max hold: `{max_hold_hours}` hours\n")
        md_file.write(f"- Max margin loss: `{settings.max_margin_loss_pct:.1%}`\n")
        md_file.write(f"- Liquidation buffer: `{settings.liquidation_buffer_pct:.1%}`\n\n")

        md_file.write("## Summary\n\n")
        md_file.write(
            "| Asset | Strategy | Lev | Trades | Win | R | Avg R | PF | Max DD | Margin | Liq |\n"
        )
        md_file.write("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        ranked = sorted(
            summaries,
            key=lambda row: (row.asset != "ALL", -row.total_margin_return_pct, -row.total_r, row.max_drawdown_r),
        )
        for row in ranked:
            md_file.write(
                f"| {row.asset} | `{row.strategy_name}` | {row.leverage:g}x | {row.trades} | "
                f"{row.win_rate:.1%} | {row.total_r:.2f} | {row.average_r:.2f} | "
                f"{row.profit_factor:.2f} | {row.max_drawdown_r:.2f} | "
                f"{row.total_margin_return_pct:.1%} | {row.liquidations} |\n"
            )

        md_file.write("\n## Recent Trades\n\n")
        md_file.write("| Asset | Entered | Exited | Outcome | Lev | Entry | Exit | R | Margin | Score |\n")
        md_file.write("|---|---|---|---|---:|---:|---:|---:|---:|---:|\n")
        for trade in sorted(trades, key=lambda trade: trade.entered_at)[-50:]:
            md_file.write(
                f"| {trade.asset} | {trade.entered_at.isoformat()} | {trade.exited_at.isoformat()} | "
                f"{trade.outcome} | {trade.leverage:g}x | {trade.entry:.6g} | {trade.exit:.6g} | "
                f"{trade.result_r:.2f} | {trade.margin_return_pct:.1%} | {trade.score} |\n"
            )

    return md_path, csv_path


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


def _print_exit_policy_summary(summary: ExitPolicySummary) -> None:
    print(
        f"{summary.asset} {summary.strategy_name} {summary.leverage:g}x {summary.policy} "
        f"trades={summary.trades} win={summary.win_rate:.1%} "
        f"R={summary.total_r:.2f} avgR={summary.average_r:.2f} pf={summary.profit_factor:.2f} "
        f"dd={summary.max_drawdown_r:.2f} margin={summary.total_margin_return_pct:.1%} "
        f"mfe_avg={summary.average_mfe_r:.2f} mfe_p75={summary.p75_mfe_r:.2f} "
        f"mfe_p90={summary.p90_mfe_r:.2f} hit5R={summary.hit_5r_rate:.1%} "
        f"hit8R={summary.hit_8r_rate:.1%} hit10R={summary.hit_10r_rate:.1%}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest a registered crypto swing strategy.")
    parser.add_argument("--strategy", default=None, help="Strategy name, or 'all' to compare every registered strategy.")
    parser.add_argument("--max-hold-hours", type=int, default=72)
    parser.add_argument("--daily-lookback-days", type=int)
    parser.add_argument("--hourly-lookback-hours", type=int)
    parser.add_argument("--leverage", type=float)
    parser.add_argument("--leverage-sweep", action="store_true", help="Run each strategy at 3x through 10x.")
    parser.add_argument("--exit-analysis", action="store_true", help="Compare score-based TP ladders, fixed targets, and runner exits.")
    parser.add_argument("--save-report", action="store_true", help="Save Markdown and CSV reports under backtests/.")
    parser.add_argument("--report-dir", default="backtests")
    parser.add_argument("--report-name")
    args = parser.parse_args()

    settings = load_settings()
    if args.daily_lookback_days is not None:
        settings = Settings(**{**settings.__dict__, "daily_lookback_days": args.daily_lookback_days})
    if args.hourly_lookback_hours is not None:
        settings = Settings(**{**settings.__dict__, "hourly_lookback_hours": args.hourly_lookback_hours})
    if args.leverage is not None:
        settings = Settings(**{**settings.__dict__, "leverage": args.leverage})

    explicit_strategy = args.strategy is not None
    has_asset_strategy = any(asset.strategy_name for asset in settings.assets)
    if args.strategy == "all":
        strategy_names = tuple(sorted(STRATEGIES))
    elif args.strategy is not None:
        strategy_names = (args.strategy,)
    elif has_asset_strategy:
        strategy_names = ("configured",)
    else:
        strategy_names = (settings.strategy_name,)
    candles_by_asset: dict[str, tuple[list[Candle], list[Candle]]] = {}
    for asset in settings.assets:
        daily = fetch_candles(asset, "1d", settings.daily_lookback_days)
        hourly = fetch_candles(asset, "1h", settings.hourly_lookback_hours)
        candles_by_asset[asset.symbol] = (daily, hourly)

    leverages = range(3, 11) if args.leverage_sweep else (settings.leverage,)
    leverage_values = tuple(float(leverage) for leverage in leverages)
    if args.exit_analysis:
        for leverage in leverage_values:
            print(f"=== exit-analysis leverage={leverage:.1f}x ===")
            for strategy_name in strategy_names:
                strategy_settings = Settings(
                    **{
                        **settings.__dict__,
                        "strategy_name": settings.strategy_name if strategy_name == "configured" else strategy_name,
                        "leverage": leverage,
                    }
                )
                for asset in settings.assets:
                    run_asset = _without_asset_strategy(asset) if explicit_strategy else asset
                    daily, hourly = candles_by_asset[asset.symbol]
                    summaries = analyze_exit_policies_asset(
                        run_asset,
                        daily,
                        hourly,
                        strategy_settings,
                        max_hold_hours=args.max_hold_hours,
                    )
                    for summary in sorted(summaries, key=lambda item: (item.total_r, item.profit_factor), reverse=True):
                        _print_exit_policy_summary(summary)
        return

    summaries: list[BacktestSummary] = []
    report_trades: list[BacktestTrade] = []
    for leverage in leverage_values:
        print(f"=== leverage={leverage:.1f}x ===")
        for strategy_name in strategy_names:
            strategy_settings = Settings(
                **{
                    **settings.__dict__,
                    "strategy_name": settings.strategy_name if strategy_name == "configured" else strategy_name,
                    "leverage": leverage,
                }
            )
            strategy_results: list[BacktestResult] = []
            for asset in settings.assets:
                run_asset = _without_asset_strategy(asset) if explicit_strategy else asset
                daily, hourly = candles_by_asset[asset.symbol]
                result = backtest_asset(run_asset, daily, hourly, strategy_settings, max_hold_hours=args.max_hold_hours)
                strategy_results.append(result)
                summaries.append(_summary(asset.symbol, result.strategy_name, leverage, result))
                report_trades.extend(result.trades)
                if not args.leverage_sweep:
                    _print_result(run_asset, result)
            combined_strategy_name = strategy_name if strategy_name != "configured" else "configured"
            combined = combine_results(combined_strategy_name, strategy_results)
            summaries.append(_summary("ALL", combined_strategy_name, leverage, combined))
            print(f"ALL {combined_strategy_name}")
            print(
                f"trades={len(combined.trades)} wins={combined.wins} losses={combined.losses} "
                f"liq={combined.liquidations} win_rate={combined.win_rate:.1%} total_r={combined.total_r:.2f} "
                f"avg_r={combined.average_r:.2f} pf={combined.profit_factor:.2f} "
                f"max_dd_r={combined.max_drawdown_r:.2f} margin_total={combined.total_margin_return_pct:.1%} "
                f"margin_avg={combined.average_margin_return_pct:.1%}"
            )

    if args.save_report:
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        report_name = args.report_name or f"{timestamp}_{'_'.join(asset.symbol for asset in settings.assets)}"
        md_path, csv_path = _write_reports(
            Path(args.report_dir),
            report_name,
            settings,
            tuple(strategy_names),
            leverage_values,
            args.max_hold_hours,
            summaries,
            report_trades,
        )
        print(f"Saved Markdown report: {md_path}")
        print(f"Saved CSV report: {csv_path}")


if __name__ == "__main__":
    main()
