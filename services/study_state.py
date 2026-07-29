"""Per-user study session state (Learn / Test)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StudySession:
    category: str | None = None
    mode: str | None = None  # learn | test | None
    test_type: str | None = None  # mcq|msq|numerical|theory
    awaiting_answer: bool = False
    current_question: dict[str, Any] | None = None
    history: list[str] = field(default_factory=list)
    score_correct: int = 0
    score_total: int = 0
    awaiting_upload_category: bool = False


_SESSIONS: dict[int, StudySession] = {}


def get(user_id: int) -> StudySession:
    if user_id not in _SESSIONS:
        _SESSIONS[user_id] = StudySession()
    return _SESSIONS[user_id]


def clear(user_id: int) -> None:
    _SESSIONS.pop(user_id, None)


def reset_mode(user_id: int) -> StudySession:
    s = get(user_id)
    s.mode = None
    s.test_type = None
    s.awaiting_answer = False
    s.current_question = None
    s.awaiting_upload_category = False
    return s


def reset_score(user_id: int) -> StudySession:
    s = get(user_id)
    s.score_correct = 0
    s.score_total = 0
    s.history.clear()
    return s


def begin_test(user_id: int, category: str, test_type: str) -> StudySession:
    s = get(user_id)
    s.category = category
    s.mode = "test"
    s.test_type = test_type
    s.awaiting_answer = False
    s.current_question = None
    s.awaiting_upload_category = False
    s.score_correct = 0
    s.score_total = 0
    s.history.clear()
    return s
