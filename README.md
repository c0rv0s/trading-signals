# Crypto Swing Alerts

Hourly swing-long scanner for a configured crypto watchlist. It combines a daily trend filter with an hourly breakout trigger and prints or sends a Telegram alert with entry, stop, take-profit levels, and blockers.

This is not financial advice and it does not predict that a move must happen. It is a rules engine for waiting until price, trend, volume, and leverage-compatible risk all line up.

## Strategy

Daily filter:

- Close above daily EMA50.
- Daily EMA20 above EMA50.
- Daily RSI in a constructive range, not euphoric.
- Price still near the prior 20-day high, so it is not deeply broken down.

Hourly trigger:

- Hourly close above EMA21 and EMA55.
- Hourly close breaks the prior 24-hour high, or holds within `MOMENTUM_CONTINUATION_MAX_PULLBACK_PCT` of that high after a strong 24-hour move.
- Breakout volume ideally above 1.25x hourly volume SMA20.
- ATR compression and higher-low structure add confidence.

Select this strategy with `STRATEGY=swing_breakout`. This is the default and the current recommended live strategy. Individual assets can override it with `{SYMBOL}_STRATEGY`, for example `WLD_STRATEGY=pullback_reclaim`.

Additional registered strategies are available for research:

- `pullback_reclaim`
- `momentum_continuation`
- `council_long`
- `volatility_contraction_breakout`
- `liquidity_sweep_reversal`
- `range_reclaim`
- `vwap_reclaim`
- `structure_retest`

Risk rules:

- Entry is the latest completed hourly close.
- Stop is the tighter of structure invalidation and 1.4x hourly ATR.
- Alerts are blocked when the stop is wider than `MAX_STOP_PCT`, tighter than `MIN_STOP_PCT`, or too close to an estimated 10x liquidation zone.
- TP levels are score-based from exit backtests. Scores 8-9 use `1.5R / 3R / 5R`; scores 10+ use `2R / 5R / 10R`.

For 10x leverage, the stop must be placed with the exchange immediately after entry. The default `MAX_STOP_PCT=0.025` means the setup is rejected if the stop is wider than 2.5% from entry.

The live defaults are leverage-aware:

- `LEVERAGE=5`
- `MAX_MARGIN_LOSS_PCT=0.12`
- `LIQUIDATION_BUFFER_PCT=0.01`
- `MOMENTUM_CONTINUATION_MAX_PULLBACK_PCT=0.015`
- `MOMENTUM_CONTINUATION_MIN_24H_GAIN_PCT=0.04`

That means a stopped trade is rejected if its price stop would risk more than 12% of isolated margin at the configured leverage, or if the stop is not at least 1% of entry above the estimated liquidation price.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Run once:

```bash
PYTHONPATH=src RUN_ONCE=true python -m crypto_swing_alerts.app
```

Run as a loop:

```bash
PYTHONPATH=src RUN_ONCE=false python -m crypto_swing_alerts.app
```

## Railway Cron

Set these environment variables in Railway:

- `TELEGRAM_BOT_TOKEN=...`
- `TELEGRAM_CHAT_ID=...`

This repo includes `railway.json`, which configures Railway to run the scanner as a cron job:

```json
{
  "deploy": {
    "startCommand": "PYTHONPATH=src python -m crypto_swing_alerts.app",
    "cronSchedule": "5 * * * *",
    "restartPolicyType": "NEVER"
  }
}
```

The schedule is UTC and runs at minute 5 of every hour. The app defaults to `RUN_ONCE=true`, so each Railway execution wakes up, fetches candles, runs analysis once, sends/prints any alerts, and exits.

Do not set `RUN_ONCE=false` for the cron service. That mode is only for a persistent worker loop.

## Telegram Behavior

The bot sends Telegram messages only when a valid entry alert is detected. No-trade analysis stays in logs.

For a manual check-in, send `yo`, `hey`, or `update` to the bot before the next cron run. If that is the latest message from the configured `TELEGRAM_CHAT_ID`, the next run sends the full analysis even when there is no trade. Each check-in request is handled once.

## Backtesting

Run a local backtest against the configured watchlist and strategy:

```bash
PYTHONPATH=src STRATEGY=swing_breakout WATCHLIST=ZEC,HYPE,BTC,SOL,NEAR,WLD python -m crypto_swing_alerts.backtest
```

Compare every registered strategy:

```bash
PYTHONPATH=src WATCHLIST=ZEC,HYPE,BTC,SOL,NEAR,WLD python -m crypto_swing_alerts.backtest --strategy all
```

Sweep leverage from 3x through 10x:

```bash
PYTHONPATH=src WATCHLIST=ZEC,HYPE,BTC,SOL,NEAR,WLD python -m crypto_swing_alerts.backtest --strategy all --leverage-sweep --hourly-lookback-hours 1000 --daily-lookback-days 365 --max-hold-hours 72
```

Save a Markdown and CSV report:

```bash
PYTHONPATH=src WATCHLIST=BTC,ETH,SOL,XMR,PENGU,XRP python -m crypto_swing_alerts.backtest --strategy all --leverage-sweep --hourly-lookback-hours 1000 --daily-lookback-days 365 --max-hold-hours 72 --save-report --report-name 2026-05-19_major_coin_sweep
```

Reports are written under `backtests/` by default.

Analyze exit policies and maximum favorable excursion:

```bash
PYTHONPATH=src WATCHLIST=HYPE STRATEGY=swing_breakout LEVERAGE=5 python -m crypto_swing_alerts.backtest --exit-analysis --hourly-lookback-hours 3000 --daily-lookback-days 365 --max-hold-hours 168
```

This compares the live score-based TP ladder against the old static ladder, fixed 3R/5R/8R/10R exits, and EMA/ATR trailing runners.

Useful knobs:

```bash
PYTHONPATH=src python -m crypto_swing_alerts.backtest --hourly-lookback-hours 1000 --daily-lookback-days 365 --max-hold-hours 72
```

The backtester walks forward one completed hourly candle at a time, evaluates the selected strategy without future candles, opens on alerts, and scores exits using the same equal-third TP ladder shown in alerts with the configured initial stop and liquidation model.

Backtest output includes R-multiple results, leveraged margin return, and liquidation counts. Liquidation is modeled conservatively from configured leverage and maintenance margin. Funding and open-interest strategies are not tested because the current data layer only fetches OHLCV candles.

New strategies should register a function in `STRATEGIES` in `src/crypto_swing_alerts/strategy.py`, then select it globally with `STRATEGY=your_strategy_name` or for one asset with `{SYMBOL}_STRATEGY=your_strategy_name`.

## Data Sources

- `BTC`, `ETH`, `HYPE`, `NEAR`, `PENGU`, `SOL`, `WLD`, `XMR`, `XRP`, and `ZEC` default to Hyperliquid perps with matching market names.
- Other symbols default to Binance spot as `<SYMBOL>USDT`.
- If Binance returns HTTP 451 for a known Hyperliquid symbol, the scanner falls back to Hyperliquid candles for that request.

Override with env vars:

```bash
WATCHLIST=ZEC,HYPE,BTC,SOL,NEAR,WLD
ZEC_PROVIDER=hyperliquid_perp
ZEC_MARKET=ZEC
BTC_PROVIDER=binance_spot
BTC_MARKET=BTCUSDT
WLD_PROVIDER=hyperliquid_perp
WLD_MARKET=WLD
WLD_STRATEGY=pullback_reclaim
```

## Exit Discipline

The alert gives score-based TP levels. A practical management rule is:

- Move stop to breakeven after TP1 or after an hourly close 1.5R above entry.
- Scale out across TP1/TP2/TP3, or trail under hourly EMA21 after TP2.
- Exit early on an hourly close back below EMA55 or a daily close back below EMA20.

Backtest these rules before increasing size. Tight 10x stops can lose repeatedly during chop even when the larger thesis is right.
