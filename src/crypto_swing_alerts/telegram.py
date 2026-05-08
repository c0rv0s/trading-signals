from __future__ import annotations

import json
from urllib.request import Request, urlopen

from .models import Signal


def format_signal(signal: Signal) -> str:
    status = "ENTRY ALERT" if signal.should_alert else "NO TRADE"
    reasons = "\n".join(f"- {reason}" for reason in signal.reasons[:8]) or "- none"
    blockers = "\n".join(f"- {blocker}" for blocker in signal.blockers[:8]) or "- none"
    return (
        f"{status}: {signal.asset} ({signal.market})\n"
        f"Score: {signal.score}\n"
        f"Entry: {signal.entry:.6g}\n"
        f"Stop: {signal.stop:.6g} ({signal.stop_pct:.2%})\n"
        f"10x estimated liquidation buffer after stop: {signal.liquidation_buffer_pct:.2%}\n"
        f"TP1: {signal.take_profit_1:.6g} | TP2: {signal.take_profit_2:.6g} | TP3: {signal.take_profit_3:.6g}\n"
        f"RR to TP2: {signal.risk_reward_to_tp2:.2f}R\n"
        f"Generated: {signal.generated_at.isoformat()}\n\n"
        f"Reasons:\n{reasons}\n\n"
        f"Blockers:\n{blockers}"
    )


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "crypto-swing-alerts/0.1"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        response.read()
