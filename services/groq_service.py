"""Groq LLM helpers for study RAG, tests, and mock interviews."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.3-70b-versatile"


def is_configured() -> bool:
    return bool(os.getenv("GROQ_API_KEY", "").strip())


def _client():
    from groq import Groq

    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return Groq(api_key=key)


def chat(
    system: str,
    user: str,
    max_tokens: int = 600,
    temperature: float = 0.7,
) -> str:
    client = _client()
    model = os.getenv("GROQ_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


def _chat(system: str, user: str, max_tokens: int = 600) -> str:
    return chat(system, user, max_tokens=max_tokens)


def generate_question(topic: str, question_num: int, history: list[dict] | None = None) -> str:
    """Ask Groq for a single interview question."""
    prior = ""
    if history:
        asked = [h.get("question", "") for h in history[-5:]]
        prior = "Avoid repeating these previous questions:\n- " + "\n- ".join(asked)

    system = (
        "You are a senior technical interviewer. Ask one clear interview question. "
        "Do not include answers, hints, or scoring. Return only the question text."
    )
    user = (
        f"Topic: {topic}\n"
        f"Question number: {question_num}\n"
        f"Difficulty: {'easy' if question_num <= 2 else 'medium' if question_num <= 4 else 'hard'}\n"
        f"{prior}"
    )
    try:
        return _chat(system, user, max_tokens=250)
    except Exception as exc:
        logger.exception("Groq generate_question failed: %s", exc)
        raise


def evaluate_answer(topic: str, question: str, answer: str) -> str:
    """Score and feedback for a candidate answer."""
    system = (
        "You are a fair technical interviewer. Evaluate the candidate's answer. "
        "Respond in this exact format:\n"
        "Score: <0-10>/10\n"
        "Feedback: <2-4 short sentences with strengths and improvements>\n"
        "Keep it concise and constructive."
    )
    user = f"Topic: {topic}\nQuestion: {question}\nCandidate answer: {answer}"
    try:
        text = _chat(system, user, max_tokens=400)
        if not re.search(r"Score:\s*\d+", text, re.I):
            text = f"Score: 5/10\nFeedback: {text}"
        return text
    except Exception as exc:
        logger.exception("Groq evaluate_answer failed: %s", exc)
        raise


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def generate_test_question(
    category_label: str,
    qtype: str,
    context: str,
    avoid: list[str] | None = None,
    difficulty: str = "medium",
) -> dict[str, Any]:
    """
    Return a structured question dict:
      type, prompt, options (list|None), correct (str|list|number), explanation
    """
    avoid_block = ""
    if avoid:
        avoid_block = "Do not repeat these prompts:\n- " + "\n- ".join(avoid[-5:])

    difficulty = (difficulty or "medium").lower()
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "medium"
    diff_guide = {
        "easy": "Beginner level: direct recall, one concept, short numbers.",
        "medium": "Standard exam level: apply a concept, one trick allowed.",
        "hard": "Advanced: multi-step, traps, or combine two ideas.",
    }[difficulty]

    schemas = {
        "mcq": (
            "Return JSON: "
            '{"type":"mcq","prompt":"...","options":["A) ...","B) ...","C) ...","D) ..."],'
            '"correct":"A","explanation":"..."}'
        ),
        "msq": (
            "Return JSON: "
            '{"type":"msq","prompt":"...","options":["A) ...","B) ...","C) ...","D) ..."],'
            '"correct":["A","C"],"explanation":"..."} '
            "(one or more correct letters)"
        ),
        "numerical": (
            "Return JSON: "
            '{"type":"numerical","prompt":"...","options":null,'
            '"correct":42,"tolerance":0.01,"explanation":"...","unit":"..."} '
            "(correct must be a number)"
        ),
        "theory": (
            "Return JSON: "
            '{"type":"theory","prompt":"...","options":null,'
            '"correct":"model answer key points","explanation":"..."}'
        ),
    }
    schema = schemas.get(qtype, schemas["mcq"])
    material = context.strip() or "(No retrieved materials — use standard syllabus knowledge.)"
    system = (
        f"You create exam-style practice questions for {category_label}. "
        "Use the study materials when provided. "
        "Output ONLY valid JSON, no markdown outside JSON."
    )
    user = (
        f"Question type: {qtype}\nDifficulty: {difficulty} — {diff_guide}\n{schema}\n\n"
        f"Materials:\n{material[:6000]}\n\n{avoid_block}"
    )
    raw = chat(system, user, max_tokens=800, temperature=0.5)
    try:
        data = _extract_json(raw)
    except Exception as exc:
        logger.warning("Failed to parse test JSON: %s | raw=%s", exc, raw[:400])
        data = {
            "type": qtype,
            "prompt": raw[:800] or "Describe a key concept from your materials.",
            "options": None,
            "correct": "",
            "explanation": "",
        }
    data["type"] = qtype
    return data


def grade_test_answer(
    category_label: str,
    qtype: str,
    question: dict[str, Any],
    student_answer: str,
) -> str:
    prompt = question.get("prompt", "")
    correct = question.get("correct")
    explanation = question.get("explanation", "")
    options = question.get("options") or []

    if qtype == "mcq":
        letter = student_answer.strip().upper()[:1]
        expected = str(correct).strip().upper()[:1]
        ok = letter == expected
        detail = explanation or f"Correct option: {expected}"
        return ("✅ Correct!\n" if ok else f"❌ Incorrect. Correct: {expected}\n") + detail

    if qtype == "msq":
        chosen = sorted({c for c in re.findall(r"[A-Da-d]", student_answer.upper())})
        expected = correct
        if isinstance(expected, str):
            expected = re.findall(r"[A-D]", expected.upper())
        expected = sorted({str(x).upper()[:1] for x in (expected or [])})
        ok = chosen == expected
        detail = explanation or f"Correct: {', '.join(expected)}"
        return (
            ("✅ Correct!\n" if ok else f"❌ Incorrect. Correct: {', '.join(expected)}\n")
            + detail
        )

    if qtype == "numerical":
        nums = re.findall(r"-?\d+(?:\.\d+)?", student_answer.replace(",", ""))
        if not nums:
            return "❌ Could not parse a number. Try again with a numeric answer."
        got = float(nums[0])
        try:
            expected = float(correct)
        except (TypeError, ValueError):
            expected = None
        tol = float(question.get("tolerance") or 0.01)
        if expected is None:
            return f"Your answer: {got}. Model answer: {correct}\n{explanation}"
        ok = abs(got - expected) <= max(tol, abs(expected) * 0.01)
        unit = question.get("unit") or ""
        unit_s = f" {unit}" if unit else ""
        return (
            ("✅ Correct!\n" if ok else f"❌ Incorrect. Expected ≈ {expected}{unit_s}\n")
            + (explanation or "")
        )

    # theory — LLM grade
    system = (
        f"You are a strict but fair examiner for {category_label}. "
        "Grade the student's theoretical answer. Respond exactly as:\n"
        "Score: <0-10>/10\n"
        "Feedback: <2-4 short sentences>\n"
        "Key points missed: <bullet-like short list or 'none'>"
    )
    user = (
        f"Question: {prompt}\n"
        f"Model answer / key points: {correct}\n"
        f"Explanation notes: {explanation}\n"
        f"Options (if any): {options}\n"
        f"Student answer: {student_answer}"
    )
    try:
        text = chat(system, user, max_tokens=450, temperature=0.3)
        if not re.search(r"Score:\s*\d+", text, re.I):
            text = f"Score: 5/10\nFeedback: {text}"
        return text
    except Exception as exc:
        logger.exception("grade theory failed: %s", exc)
        raise
