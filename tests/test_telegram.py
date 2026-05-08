import json
import unittest
from unittest.mock import patch

from crypto_swing_alerts.telegram import acknowledge_telegram_update, latest_check_in_update_id


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
