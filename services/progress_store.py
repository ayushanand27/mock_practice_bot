"""Persisted per-user study progress: streak, stats, wrong answers."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from config import DATA_DIR

_PATH = DATA_DIR / "progress.json"
_LOCK = threading.Lock()


def _today() -> str:
    return date.today().isoformat()


def _load() -> dict[str, Any]:
    if not _PATH.exists():
        return {}
    try:
        return json.loads(_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _user(data: dict[str, Any], user_id: int) -> dict[str, Any]:
    key = str(user_id)
    if key not in data:
        data[key] = {
            "streak": 0,
            "last_active": None,
            "daily_goal": 10,
            "daily_answered": 0,
            "daily_date": None,
            "total_answered": 0,
            "total_correct": 0,
            "by_category": {},
            "wrong": [],
        }
    return data[key]


def _roll_day(u: dict[str, Any]) -> None:
    today = _today()
    if u.get("daily_date") != today:
        last = u.get("last_active")
        if last:
            try:
                last_d = date.fromisoformat(last)
                delta = (date.today() - last_d).days
                if delta == 1:
                    u["streak"] = int(u.get("streak") or 0) + 1
                elif delta > 1:
                    u["streak"] = 1
            except ValueError:
                u["streak"] = 1
        else:
            u["streak"] = 1
        u["daily_date"] = today
        u["daily_answered"] = 0
        u["last_active"] = today


def record_answer(
    user_id: int,
    *,
    category: str,
    qtype: str,
    correct: bool,
    prompt: str,
    explanation: str = "",
    student_answer: str = "",
) -> dict[str, Any]:
    with _LOCK:
        data = _load()
        u = _user(data, user_id)
        _roll_day(u)
        u["daily_answered"] = int(u.get("daily_answered") or 0) + 1
        u["total_answered"] = int(u.get("total_answered") or 0) + 1
        if correct:
            u["total_correct"] = int(u.get("total_correct") or 0) + 1
        cat = u.setdefault("by_category", {}).setdefault(
            category, {"answered": 0, "correct": 0}
        )
        cat["answered"] = int(cat.get("answered") or 0) + 1
        if correct:
            cat["correct"] = int(cat.get("correct") or 0) + 1
        if not correct and prompt:
            wrong = u.setdefault("wrong", [])
            wrong.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "category": category,
                    "qtype": qtype,
                    "prompt": prompt[:500],
                    "explanation": (explanation or "")[:400],
                    "your_answer": (student_answer or "")[:200],
                }
            )
            u["wrong"] = wrong[-30:]  # keep last 30
        _save(data)
        return dict(u)


def get_stats(user_id: int) -> dict[str, Any]:
    with _LOCK:
        data = _load()
        u = _user(data, user_id)
        _roll_day(u)
        _save(data)
        return dict(u)


def pop_wrong(user_id: int, n: int = 1) -> list[dict[str, Any]]:
    with _LOCK:
        data = _load()
        u = _user(data, user_id)
        wrong = list(u.get("wrong") or [])
        if not wrong:
            return []
        take = wrong[-n:]
        u["wrong"] = wrong[:-n] if n < len(wrong) else []
        _save(data)
        return take


def list_wrong(user_id: int, limit: int = 5) -> list[dict[str, Any]]:
    with _LOCK:
        data = _load()
        u = _user(data, user_id)
        wrong = list(u.get("wrong") or [])
        return wrong[-limit:]


def format_stats(user_id: int) -> str:
    u = get_stats(user_id)
    total = int(u.get("total_answered") or 0)
    correct = int(u.get("total_correct") or 0)
    acc = f"{(100 * correct / total):.0f}%" if total else "—"
    goal = int(u.get("daily_goal") or 10)
    daily = int(u.get("daily_answered") or 0)
    bar_n = min(10, int(10 * daily / goal)) if goal else 0
    bar = "█" * bar_n + "░" * (10 - bar_n)
    lines = [
        "📊 Your progress",
        f"🔥 Streak: {u.get('streak', 0)} day(s)",
        f"🎯 Today: {daily}/{goal}  [{bar}]",
        f"✅ Lifetime: {correct}/{total} ({acc})",
    ]
    by_cat = u.get("by_category") or {}
    if by_cat:
        lines.append("\nBy category:")
        for cat, s in sorted(by_cat.items()):
            a, c = int(s.get("answered") or 0), int(s.get("correct") or 0)
            lines.append(f"• {cat}: {c}/{a}")
    wrong_n = len(u.get("wrong") or [])
    if wrong_n:
        lines.append(f"\n📝 Saved mistakes: {wrong_n} (use Review mistakes)")
    return "\n".join(lines)
