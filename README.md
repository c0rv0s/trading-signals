# Crypto Swing Alerts

Hourly swing-long scanner for `ZEC` and `HYPE`. It combines a daily trend filter with an hourly breakout trigger and prints or sends a Telegram alert with entry, stop, take-profit levels, and blockers.

This is not financial advice and it does not predict that a move must happen. It is a rules engine for waiting until price, trend, volume, and leverage-compatible risk all line up.

## Strategy

Daily filter:

- Close above daily EMA50.
- Daily EMA20 above EMA50.
- Daily RSI in a constructive range, not euphoric.
- Price still near the prior 20-day high, so it is not deeply broken down.

Hourly trigger:

- Hourly close above EMA21 and EMA55.
- Hourly close breaks the prior 24-hour high.
- Breakout volume ideally above 1.25x hourly volume SMA20.
- ATR compression and higher-low structure add confidence.

Risk rules:

- Entry is the latest completed hourly close.
- Stop is the tighter of structure invalidation and 1.4x hourly ATR.
- Alerts are blocked when the stop is wider than `MAX_STOP_PCT`, tighter than `MIN_STOP_PCT`, or too close to an estimated 10x liquidation zone.
- TP1 is 1.5R, TP2 is 3R, TP3 is 5R.

For 10x leverage, the stop must be placed with the exchange immediately after entry. The default `MAX_STOP_PCT=0.045` means the setup is rejected if the stop is wider than 4.5% from entry.

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

## Railway

Set these environment variables in Railway:

- `PYTHONPATH=src`
- `RUN_ONCE=false`
- `TELEGRAM_BOT_TOKEN=...`
- `TELEGRAM_CHAT_ID=...`

Railway will use the `Procfile`:

```text
worker: python -m crypto_swing_alerts.app
```

## Data Sources

- `ZEC` defaults to Binance spot `ZECUSDT`.
- `HYPE` defaults to Hyperliquid perp `HYPE`.

Override with env vars:

```bash
WATCHLIST=ZEC,HYPE
ZEC_PROVIDER=binance_spot
ZEC_MARKET=ZECUSDT
HYPE_PROVIDER=hyperliquid_perp
HYPE_MARKET=HYPE
```

## Exit Discipline

The alert gives static TP levels. A practical management rule is:

- Move stop to breakeven after TP1 or after an hourly close 1.5R above entry.
- Exit fully at TP2/TP3, or trail under hourly EMA21 after TP2.
- Exit early on an hourly close back below EMA55 or a daily close back below EMA20.

Backtest these rules before increasing size. Tight 10x stops can lose repeatedly during chop even when the larger thesis is right.
