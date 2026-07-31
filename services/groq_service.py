"""LLM helpers for study RAG, tests, and mock interviews.

Provider order: Groq → Gemini (optional) → Sarvam → local extractive fallback.
Groq often returns 403 from cloud datacenter IPs (e.g. Azure); fallbacks keep the bot usable.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.3-70b-versatile"
SARVAM_MODEL = "sarvam-105b"
GEMINI_MODEL = "gemini-2.0-flash"


def is_configured() -> bool:
    """True if any generative path can run (including local fallback)."""
    return True


def has_cloud_llm() -> bool:
    return bool(
        os.getenv("GROQ_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
        or os.getenv("SARVAM_API_KEY", "").strip()
    )


def _groq_chat(system: str, user: str, max_tokens: int, temperature: float) -> str:
    from groq import Groq

    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("no groq key")
    client = Groq(api_key=key)
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


def _gemini_chat(system: str, user: str, max_tokens: int, temperature: float) -> str:
    key = (
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )
    if not key:
        raise RuntimeError("no gemini key")
    model = os.getenv("GEMINI_MODEL", GEMINI_MODEL).strip() or GEMINI_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    parts = (
        ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    )
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise RuntimeError("empty gemini response")
    return text


def _sarvam_chat(system: str, user: str, max_tokens: int, temperature: float) -> str:
    key = os.getenv("SARVAM_API_KEY", "").strip()
    if not key:
        raise RuntimeError("no sarvam key")
    # Reasoning models need headroom so content is not truncated to null
    tok = max(max_tokens, 512)
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            "https://api.sarvam.ai/v1/chat/completions",
            headers={
                "api-subscription-key": key,
                "Content-Type": "application/json",
            },
            json={
                "model": os.getenv("SARVAM_CHAT_MODEL", SARVAM_MODEL),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": tok,
                "reasoning_effort": "low",
            },
        )
        r.raise_for_status()
        data = r.json()
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    text = (msg.get("content") or "").strip()
    if not text:
        # Fall back to last non-empty lines of reasoning if model only reasoned
        reasoning = (msg.get("reasoning_content") or "").strip()
        lines = [ln.strip() for ln in reasoning.splitlines() if ln.strip()]
        text = "\n".join(lines[-8:]) if lines else ""
    if not text:
        raise RuntimeError("empty sarvam response")
    return text


def _local_chat(system: str, user: str) -> str:
    """Extractive fallback when cloud LLMs are unavailable (e.g. Groq 403 on Azure)."""
    # Prefer study-materials block if present
    materials = ""
    m = re.search(r"Study materials:\n([\s\S]*?)\n\nStudent question:\n", user)
    if m:
        materials = m.group(1)
    if not materials:
        m2 = re.search(r"Materials:\n([\s\S]*?)(?:\n\nDo not repeat|\Z)", user)
        if m2:
            materials = m2.group(1)
    question = ""
    q = re.search(r"Student question:\n([\s\S]+)$", user)
    if q:
        question = q.group(1).strip()
    chunks = [c.strip() for c in re.split(r"\n---\n", materials) if c.strip()]
    if not chunks and materials.strip():
        chunks = [materials.strip()]
    if not chunks:
        return (
            "Cloud AI is temporarily unavailable from this server, and I have no "
            "retrieved notes for this query. Upload materials or try again later."
        )
    # Keyword overlap ranking
    words = {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", question)}
    scored: list[tuple[int, str]] = []
    for ch in chunks:
        score = sum(1 for w in words if w in ch.lower())
        scored.append((score, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [c for _, c in scored[:2]] or chunks[:1]
    body = "\n\n".join(top)
    if len(body) > 2800:
        body = body[:2790] + "…"
    return (
        "📚 From your notes (offline mode — cloud LLM blocked from this host):\n\n"
        f"{body}\n\n"
        "Tip: Add a free Gemini key (GEMINI_API_KEY) in .env for smarter answers on Azure."
    )


def chat(
    system: str,
    user: str,
    max_tokens: int = 600,
    temperature: float = 0.7,
) -> str:
    errors: list[str] = []
    for name, fn in (
        ("groq", lambda: _groq_chat(system, user, max_tokens, temperature)),
        ("gemini", lambda: _gemini_chat(system, user, max_tokens, temperature)),
        ("sarvam", lambda: _sarvam_chat(system, user, max_tokens, temperature)),
    ):
        try:
            text = fn()
            if text:
                if name != "groq":
                    logger.info("LLM via %s", name)
                return text
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            logger.warning("LLM provider %s failed: %s", name, exc)
    logger.error("All cloud LLMs failed (%s); using local fallback", "; ".join(errors))
    return _local_chat(system, user)


def _chat(system: str, user: str, max_tokens: int = 600) -> str:
    return chat(system, user, max_tokens=max_tokens)


def generate_question(topic: str, question_num: int, history: list[dict] | None = None) -> str:
    """Ask for a single interview question."""
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
        text = _chat(system, user, max_tokens=250)
        if text.startswith("📚 From your notes"):
            return f"Explain a key concept in {topic} and give one practical example."
        return text
    except Exception as exc:
        logger.exception("generate_question failed: %s", exc)
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
        if text.startswith("📚 From your notes"):
            return (
                "Score: 5/10\n"
                "Feedback: Offline mode — review your answer against standard "
                f"{topic} interview expectations and add concrete examples."
            )
        if not re.search(r"Score:\s*\d+", text, re.I):
            text = f"Score: 5/10\nFeedback: {text}"
        return text
    except Exception as exc:
        logger.exception("evaluate_answer failed: %s", exc)
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


def _local_test_question(qtype: str, context: str, difficulty: str) -> dict[str, Any]:
    """Build a simple practice question from retrieved text without an LLM."""
    # Strip source tags
    plain = re.sub(r"\[[^\]]+\]\n", "", context)
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", plain)
        if 40 <= len(s.strip()) <= 220
    ]
    if not sentences:
        sentences = [plain[:200].strip() or "Core concept from your notes."]
    fact = random.choice(sentences)
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", fact)
    blank = words[min(3, len(words) - 1)] if words else "concept"
    if qtype == "theory":
        return {
            "type": "theory",
            "prompt": f"In your own words, explain: {fact[:160]}",
            "options": None,
            "correct": fact,
            "explanation": "Compare your answer to the note excerpt.",
        }
    if qtype == "numerical":
        nums = re.findall(r"-?\d+(?:\.\d+)?", fact)
        if nums:
            n = float(nums[0])
            return {
                "type": "numerical",
                "prompt": f"From the notes, what number appears in: “{fact[:140]}”?",
                "options": None,
                "correct": n,
                "tolerance": 0.01,
                "explanation": f"Taken from your materials ({difficulty}).",
                "unit": "",
            }
        return {
            "type": "numerical",
            "prompt": "How many key points did you study in the last section? Enter an integer estimate.",
            "options": None,
            "correct": 3,
            "tolerance": 2,
            "explanation": "Offline placeholder numerical.",
            "unit": "",
        }
    # mcq / msq
    correct_opt = fact[:70] + ("…" if len(fact) > 70 else "")
    distractors = []
    for s in sentences:
        if s != fact:
            distractors.append(s[:70] + ("…" if len(s) > 70 else ""))
        if len(distractors) >= 3:
            break
    while len(distractors) < 3:
        distractors.append(f"Unrelated {difficulty} distractor {len(distractors)+1}")
    options_text = [correct_opt] + distractors[:3]
    random.shuffle(options_text)
    letters = ["A", "B", "C", "D"]
    labeled = [f"{letters[i]}) {options_text[i]}" for i in range(4)]
    correct_letter = letters[options_text.index(correct_opt)]
    if qtype == "msq":
        return {
            "type": "msq",
            "prompt": f"Which statement(s) match your notes about “{blank}\"?",
            "options": labeled,
            "correct": [correct_letter],
            "explanation": f"Supported by: {fact[:180]}",
        }
    return {
        "type": "mcq",
        "prompt": f"Which statement best matches your notes ({difficulty})?",
        "options": labeled,
        "correct": correct_letter,
        "explanation": f"Supported by: {fact[:180]}",
    }


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
    if raw.startswith("📚 From your notes") or "offline mode" in raw.lower():
        return _local_test_question(qtype, context, difficulty)
    try:
        data = _extract_json(raw)
    except Exception as exc:
        logger.warning("Failed to parse test JSON: %s | raw=%s", exc, raw[:400])
        return _local_test_question(qtype, context, difficulty)
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

    # theory — LLM grade (falls back offline)
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
        if text.startswith("📚 From your notes"):
            return (
                "Score: 6/10\n"
                "Feedback: Offline grading — compare your answer to the model points "
                "in the explanation. Add missing definitions and examples.\n"
                f"Key points missed: review → {str(correct)[:200]}"
            )
        if not re.search(r"Score:\s*\d+", text, re.I):
            text = f"Score: 5/10\nFeedback: {text}"
        return text
    except Exception as exc:
        logger.exception("grade theory failed: %s", exc)
        raise
