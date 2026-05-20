from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Signal

CHECK_IN_COMMANDS = {"hey", "update", "yo"}
CHECK_IN_STATE_KEY = "telegram:last_check_in_update_id"


def _target_r(signal: Signal, target: float) -> float:
    risk = signal.risk_per_unit
    return 0.0 if risk <= 0 else (target - signal.entry) / risk


def _format_r(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 0.05:
        return f"{rounded:.0f}R"
    return f"{value:.1f}R"


def format_signal(signal: Signal) -> str:
    status = "ENTRY ALERT" if signal.should_alert else "NO TRADE"
    reasons = "\n".join(f"- {reason}" for reason in signal.reasons[:8]) or "- none"
    blockers = "\n".join(f"- {blocker}" for blocker in signal.blockers[:8]) or "- none"
    tp1_r = _format_r(_target_r(signal, signal.take_profit_1))
    tp2_r = _format_r(_target_r(signal, signal.take_profit_2))
    tp3_r = _format_r(_target_r(signal, signal.take_profit_3))
    return (
        f"{status}: {signal.asset} ({signal.market})\n"
        f"Score: {signal.score}\n"
        f"Entry: {signal.entry:.6g}\n"
        f"Stop: {signal.stop:.6g} ({signal.stop_pct:.2%})\n"
        f"Estimated liquidation buffer after stop: {signal.liquidation_buffer_pct:.2%}\n"
        f"TP1 ({tp1_r}): {signal.take_profit_1:.6g} | "
        f"TP2 ({tp2_r}): {signal.take_profit_2:.6g} | "
        f"TP3 ({tp3_r}): {signal.take_profit_3:.6g}\n"
        f"RR to TP2: {signal.risk_reward_to_tp2:.2f}R\n"
        f"Generated: {signal.generated_at.isoformat()}\n\n"
        f"Reasons:\n{reasons}\n\n"
        f"Blockers:\n{blockers}"
    )


def latest_check_in_update_id(bot_token: str, chat_id: str) -> int | None:
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/getUpdates",
        headers={"User-Agent": "crypto-swing-alerts/0.1"},
        method="GET",
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    updates = payload.get("result", [])
    for update in reversed(updates):
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat", {})
        if str(chat.get("id")) != str(chat_id):
            continue
        text = str(message.get("text", "")).strip().lower()
        if text in CHECK_IN_COMMANDS:
            return int(update.get("update_id", 0))
        return None
    return None


def acknowledge_telegram_update(bot_token: str, update_id: int) -> None:
    query = urlencode({"offset": update_id + 1, "limit": 1})
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/getUpdates?{query}",
        headers={"User-Agent": "crypto-swing-alerts/0.1"},
        method="GET",
    )
    with urlopen(request, timeout=20) as response:
        response.read()


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "crypto-swing-alerts/0.1"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        response.read()
