import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from crypto_swing_alerts.models import Signal
from crypto_swing_alerts.telegram import acknowledge_telegram_update, format_signal, latest_check_in_update_id


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class TelegramTests(unittest.TestCase):
    def test_format_signal_labels_take_profit_r_multiples(self) -> None:
        signal = Signal(
            asset="HYPE",
            market="HYPE",
            provider="hyperliquid_perp",
            score=11,
            should_alert=True,
            entry=100.0,
            stop=98.0,
            stop_pct=0.02,
            liquidation_buffer_pct=0.12,
            take_profit_1=103.0,
            take_profit_2=106.0,
            take_profit_3=120.0,
            risk_reward_to_tp2=3.0,
            reasons=("breakout confirmed",),
            blockers=(),
            generated_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )

        message = format_signal(signal)

        self.assertIn("TP1 (1.5R): 103", message)
        self.assertIn("TP2 (3R): 106", message)
        self.assertIn("TP3 (10R): 120", message)

    def test_latest_check_in_update_id_matches_latest_chat_command(self) -> None:
        payload = {
            "ok": True,
            "result": [
                {"update_id": 10, "message": {"chat": {"id": 123}, "text": "yo"}},
                {"update_id": 11, "message": {"chat": {"id": 456}, "text": "update"}},
                {"update_id": 12, "message": {"chat": {"id": 123}, "text": "hey"}},
            ],
        }

        with patch("crypto_swing_alerts.telegram.urlopen", return_value=_Response(payload)):
            self.assertEqual(latest_check_in_update_id("token", "123"), 12)

    def test_latest_check_in_update_id_ignores_non_command_latest_message(self) -> None:
        payload = {
            "ok": True,
            "result": [
                {"update_id": 10, "message": {"chat": {"id": 123}, "text": "yo"}},
                {"update_id": 11, "message": {"chat": {"id": 123}, "text": "thanks"}},
            ],
        }

        with patch("crypto_swing_alerts.telegram.urlopen", return_value=_Response(payload)):
            self.assertIsNone(latest_check_in_update_id("token", "123"))

    def test_acknowledge_telegram_update_advances_offset(self) -> None:
        with patch("crypto_swing_alerts.telegram.urlopen", return_value=_Response({"ok": True})) as urlopen:
            acknowledge_telegram_update("token", 42)

        request = urlopen.call_args.args[0]
        self.assertIn("offset=43", request.full_url)


if __name__ == "__main__":
    unittest.main()
