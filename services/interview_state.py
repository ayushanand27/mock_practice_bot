"""In-memory mock-interview session state (v1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InterviewSession:
    topic: str
    question_num: int = 0
    current_question: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    active: bool = True


_sessions: dict[int, InterviewSession] = {}


def get_session(user_id: int) -> InterviewSession | None:
    return _sessions.get(user_id)


def start_session(user_id: int, topic: str) -> InterviewSession:
    session = InterviewSession(topic=topic)
    _sessions[user_id] = session
    return session


def end_session(user_id: int) -> InterviewSession | None:
    return _sessions.pop(user_id, None)


def is_active(user_id: int) -> bool:
    session = _sessions.get(user_id)
    return bool(session and session.active)
