from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


class SignalState:
    def __init__(self, path: Path, cooldown_hours: int) -> None:
        self.path = path
        self.cooldown = timedelta(hours=cooldown_hours)
        self.data = self._load()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(key): str(value) for key, value in raw.items()}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True))

    def can_alert(self, key: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(tz=timezone.utc)
        previous = self.data.get(key)
        if previous is None:
            return True
        try:
            previous_time = datetime.fromisoformat(previous)
        except ValueError:
            return True
        return now - previous_time >= self.cooldown

    def mark_alerted(self, key: str, now: datetime | None = None) -> None:
        now = now or datetime.now(tz=timezone.utc)
        self.data[key] = now.isoformat()
        self._save()
