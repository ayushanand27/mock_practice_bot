"""Simple JSON persistence for per-user notes."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "notes.json"


class NotesStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _DEFAULT_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({})

    def _read(self) -> dict:
        with _LOCK:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}

    def _write(self, data: dict) -> None:
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def add(self, user_id: int, text: str) -> dict:
        data = self._read()
        key = str(user_id)
        notes = data.setdefault(key, [])
        note = {
            "id": (notes[-1]["id"] + 1) if notes else 1,
            "text": text.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        notes.append(note)
        self._write(data)
        return note

    def list(self, user_id: int) -> list[dict]:
        return list(self._read().get(str(user_id), []))

    def clear(self, user_id: int) -> int:
        data = self._read()
        key = str(user_id)
        count = len(data.get(key, []))
        data[key] = []
        self._write(data)
        return count


notes_store = NotesStore()
