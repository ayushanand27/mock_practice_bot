"""Per-user study session state (Learn / Test)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StudySession:
    category: str | None = None
    topic: str | None = None  # topic slug within category
    mode: str | None = None  # learn | test | mistakes | chat | mock | revise | None
    test_type: str | None = None  # mcq|msq|numerical|theory
    difficulty: str = "medium"  # easy | medium | hard
    awaiting_answer: bool = False
    current_question: dict[str, Any] | None = None
    history: list[str] = field(default_factory=list)
    score_correct: int = 0
    score_total: int = 0
    awaiting_upload_category: bool = False
    # session report trail: {correct, qtype, prompt, topic}
    session_log: list[dict[str, Any]] = field(default_factory=list)
    last_learn_answer: str = ""
    voice_learn: bool = True
    offline_notice_shown: bool = False
    # Timed mock exam
    mock_target: int = 0
    mock_started_at: float = 0.0
    # Flashcard / revise
    revise_cards: list[dict] = field(default_factory=list)
    revise_index: int = 0


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
    s.last_learn_answer = ""
    return s


def reset_score(user_id: int) -> StudySession:
    s = get(user_id)
    s.score_correct = 0
    s.score_total = 0
    s.history.clear()
    s.session_log.clear()
    return s


def begin_test(
    user_id: int,
    category: str,
    test_type: str,
    *,
    reset_score: bool = True,
    difficulty: str | None = None,
    topic: str | None = None,
    mode: str = "test",
) -> StudySession:
    s = get(user_id)
    s.category = category
    if topic is not None:
        s.topic = topic
    s.mode = mode
    s.test_type = test_type
    if difficulty:
        s.difficulty = difficulty
    s.awaiting_answer = False
    s.current_question = None
    s.awaiting_upload_category = False
    if reset_score:
        s.score_correct = 0
        s.score_total = 0
        s.history.clear()
        s.session_log.clear()
    return s
