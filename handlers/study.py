"""Study flow: category → Learn / Test, RAG Q&A, tests, upload, reindex."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import CATEGORIES, SUPPORTED_EXTS, category_label, topic_label, uploads_path
from keyboards import (
    categories_keyboard,
    difficulty_keyboard,
    learn_controls_keyboard,
    main_reply_keyboard,
    mock_controls_keyboard,
    mode_keyboard,
    revise_controls_keyboard,
    test_controls_keyboard,
    test_types_keyboard,
    topics_keyboard,
    upload_category_keyboard,
)
from rag import pipeline
from rag import store as rag_store
from services import groq_service, guardrails, language, progress_store, rate_limit, sarvam_service, study_state

logger = logging.getLogger(__name__)

TG_MAX = 3900
MOCK_DEFAULT_QUESTIONS = 15
MOCK_SECONDS_PER_Q = 90


async def _answer(query, text: str | None = None, **kwargs) -> None:
    """Ack a callback query (never recurse)."""
    try:
        if text is None:
            await query.answer(**kwargs)
        else:
            await query.answer(text, **kwargs)
    except BadRequest as exc:
        msg = str(exc).lower()
        if "too old" in msg or "query id is invalid" in msg:
            logger.info("Ignoring stale callback: %s", exc)
            return
        raise


def _clip(text: str, limit: int = TG_MAX) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _uid(update: Update) -> int:
    user = update.effective_user
    return user.id if user else 0


async def _typing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat:
        try:
            await context.bot.send_chat_action(chat.id, ChatAction.TYPING)
        except Exception:  # noqa: BLE001
            pass


async def _reply(update: Update, text: str, **kwargs) -> None:
    text = _clip(text)
    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, **kwargs)
    elif update.message:
        await update.message.reply_text(text, **kwargs)


def _chunk_count(category: str) -> int:
    try:
        return int(rag_store.count(category))
    except Exception:  # noqa: BLE001
        return 0


def _empty_materials_note(category: str) -> str:
    n = _chunk_count(category)
    if n > 0:
        return f"Indexed chunks: {n}. Upload more PDFs anytime for stronger answers."
    return (
        "⚠️ No materials indexed for this category yet.\n"
        "• Tap *Upload* → pick category → send a PDF (best results)\n"
        "• Or ask the owner to add files under data/materials/ and run /reindex\n"
        "You can still browse topics — Learn/Test work once materials exist."
    )


def _mock_timer_line(sess: study_state.StudySession) -> str:
    if sess.mode != "mock" or not sess.mock_target:
        return ""
    n = sess.score_total + (1 if sess.awaiting_answer else 0)
    elapsed = int(time.time() - sess.mock_started_at) if sess.mock_started_at else 0
    budget = sess.mock_target * MOCK_SECONDS_PER_Q
    left = max(0, budget - elapsed)
    mins, secs = divmod(left, 60)
    return f"⏱ Mock Q{n}/{sess.mock_target} · ~{mins}m {secs:02d}s left\n"


def _focus_hint(sess: study_state.StudySession) -> str:
    if not sess.category:
        return ""
    return f"Focus: {topic_label(sess.category, sess.topic)}"


def _maybe_offline_notice(sess: study_state.StudySession, text: str) -> str:
    """Prepend a short notes-mode line once per session when cloud LLM is unavailable."""
    offline = groq_service.is_offline_response(text) or groq_service.chat_used_offline()
    if not offline:
        return text
    if sess.offline_notice_shown:
        return text
    sess.offline_notice_shown = True
    return f"{groq_service.OFFLINE_USER_NOTE}\n\n{text}"


def format_session_report(
    sess: study_state.StudySession, *, user_id: int = 0
) -> str:
    total = sess.score_total
    correct = sess.score_correct
    if total == 0:
        return "Session report: no questions answered."
    acc = 100 * correct / total
    topic = topic_label(sess.category or "", sess.topic) if sess.topic else "All topics"
    streak = 0
    if user_id:
        streak = int(progress_store.get_stats(user_id).get("streak") or 0)
    emoji = "🔥" if acc >= 80 else "📈" if acc >= 50 else "💪"
    lines = [
        f"{emoji} Study session — {category_label(sess.category or '')}",
        f"Score: {correct}/{total} ({acc:.0f}%) · {sess.difficulty.title()} · {topic}",
    ]
    if streak:
        lines.append(f"🔥 {streak}-day streak")
    lines.append("")
    lines.append("Practice with @mock_practice_bot on Telegram")
    wrongs = [x for x in sess.session_log if not x.get("correct")]
    if wrongs:
        lines.append(f"\nMissed {len(wrongs)} — tap Review or Practice mistakes.")
    else:
        lines.append("\nPerfect session — no mistakes!")
    lines.append("\n#StudyBot #ExamPrep")
    return "\n".join(lines)


async def study_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    study_state.reset_mode(uid)
    text = (
        "📚 *Study mode*\n\n"
        "Pick a category. Then choose *Learn* (ask concepts from your materials) "
        "or *Test* (MCQ / MSQ / Numerical / Theory)."
    )
    if update.callback_query:
        await _answer(update.callback_query)
        await update.callback_query.edit_message_text(
            text, reply_markup=categories_keyboard(), parse_mode="Markdown"
        )
    else:
        await _reply(
            update,
            text,
            reply_markup=categories_keyboard(),
            parse_mode="Markdown",
        )


async def study_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await study_home(update, context)


async def mock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start timed mock exam for current category or prompt to pick one."""
    uid = _uid(update)
    sess = study_state.get(uid)
    if not sess.category:
        await _reply(
            update,
            "Pick a category first: /study → category → ⏱ Mock exam",
            reply_markup=categories_keyboard(),
        )
        return
    cat = sess.category
    study_state.begin_test(uid, cat, "mcq", difficulty="medium", mode="mock")
    s = study_state.get(uid)
    s.mock_target = MOCK_DEFAULT_QUESTIONS
    s.mock_started_at = time.time()
    await _reply(
        update,
        f"⏱ *Mock exam* — {category_label(cat)}\n"
        f"{MOCK_DEFAULT_QUESTIONS} MCQ · ~{MOCK_DEFAULT_QUESTIONS * MOCK_SECONDS_PER_Q // 60} min.\n"
        "Starting Q1…",
        parse_mode="Markdown",
    )
    await _send_test_question(update, context)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle study-related callbacks. Returns True if handled."""
    query = update.callback_query
    if not query or not query.data:
        return False
    data = query.data
    uid = _uid(update)

    if data == "study:home":
        await study_home(update, context)
        return True

    if data == "study:stats":
        await _answer(query)
        await _reply(update, progress_store.format_stats(uid), reply_markup=main_reply_keyboard())
        return True

    if data == "study:review":
        await _answer(query)
        await show_review(update, context)
        return True

    if data == "study:switch_test":
        sess = study_state.get(uid)
        if not sess.category:
            await _answer(query, "Pick a category first")
            await study_home(update, context)
            return True
        sess.mode = "test"
        sess.awaiting_answer = False
        sess.current_question = None
        await _answer(query)
        await query.edit_message_text(
            f"📝 Test — {category_label(sess.category)}\n"
            f"{_empty_materials_note(sess.category)}\n\nChoose question type:",
            reply_markup=test_types_keyboard(sess.category),
        )
        return True

    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        if cat not in CATEGORIES:
            await _answer(query, "Unknown category")
            return True
        sess = study_state.get(uid)
        sess.category = cat
        sess.topic = None
        sess.mode = None
        sess.awaiting_answer = False
        sess.awaiting_upload_category = False
        await _answer(query)
        await query.edit_message_text(
            f"Category: *{category_label(cat)}*\n\n"
            f"{_empty_materials_note(cat)}\n\n"
            "Pick a *topic/chapter* (or All topics):",
            reply_markup=topics_keyboard(cat),
            parse_mode="Markdown",
        )
        return True

    if data.startswith("top:"):
        # top:ncert_11:phys | top:ncert_11:all
        parts = data.split(":")
        if len(parts) != 3:
            await _answer(query)
            return True
        _, cat, topic = parts
        if cat not in CATEGORIES:
            await _answer(query, "Unknown category")
            return True
        sess = study_state.get(uid)
        sess.category = cat
        sess.topic = None if topic == "all" else topic
        sess.mode = None
        await _answer(query)
        await query.edit_message_text(
            f"*{category_label(cat)}* · {_focus_hint(sess)}\n\n"
            f"{_empty_materials_note(cat)}\n\n"
            "Choose Learn, Test, or Practice mistakes:",
            reply_markup=mode_keyboard(cat),
            parse_mode="Markdown",
        )
        return True

    if data == "study:retopic":
        sess = study_state.get(uid)
        if not sess.category:
            await study_home(update, context)
            return True
        await _answer(query)
        await query.edit_message_text(
            f"Pick a topic for *{category_label(sess.category)}*:",
            reply_markup=topics_keyboard(sess.category),
            parse_mode="Markdown",
        )
        return True

    if data == "study:mistakes_now":
        sess = study_state.get(uid)
        cat = sess.category
        if not cat:
            await _answer(query, "Pick a category first")
            await study_home(update, context)
            return True
        await _answer(query)
        await _start_mistakes_practice(update, context, cat)
        return True

    if data == "learn:voice":
        await _answer(query, "Generating voice…")
        await _speak_last_learn(update, context)
        return True

    if data.startswith("mode:"):
        # mode:learn:placement | mode:test:placement | mode:mistakes:placement
        # mode:mock:placement | mode:revise:placement
        parts = data.split(":")
        if len(parts) != 3:
            await _answer(query)
            return True
        _, mode, cat = parts
        if cat not in CATEGORIES:
            await _answer(query, "Unknown category")
            return True
        sess = study_state.get(uid)
        sess.category = cat
        sess.awaiting_answer = False
        sess.current_question = None
        sess.awaiting_upload_category = False
        await _answer(query)
        if mode == "mistakes":
            await _start_mistakes_practice(update, context, cat)
            return True
        if mode == "mock":
            study_state.begin_test(uid, cat, "mcq", difficulty="medium", mode="mock")
            s = study_state.get(uid)
            s.mock_target = MOCK_DEFAULT_QUESTIONS
            s.mock_started_at = time.time()
            await query.edit_message_text(
                f"⏱ *Mock exam* — {category_label(cat)}\n"
                f"{_focus_hint(s)}\n"
                f"{_empty_materials_note(cat)}\n\n"
                f"{MOCK_DEFAULT_QUESTIONS} MCQ questions · ~{MOCK_DEFAULT_QUESTIONS * MOCK_SECONDS_PER_Q // 60} min budget.\n"
                "Starting Q1…",
                parse_mode="Markdown",
            )
            await _send_test_question(update, context)
            return True
        if mode == "revise":
            await _start_revise(update, context, cat)
            return True
        sess.mode = mode
        if mode == "learn":
            await query.edit_message_text(
                f"📖 *Learn — {category_label(cat)}*\n"
                f"{_focus_hint(sess)}\n\n"
                f"{_empty_materials_note(cat)}\n\n"
                "Ask any concept question. I'll answer from your materials "
                "(and can speak the answer — 🔊 Hear answer).\n"
                "Examples: \"What is ACID?\", \"Explain projectile motion\".",
                parse_mode="Markdown",
                reply_markup=learn_controls_keyboard(),
            )
        else:
            await query.edit_message_text(
                f"📝 *Test — {category_label(cat)}*\n"
                f"{_focus_hint(sess)}\n"
                f"{_empty_materials_note(cat)}\n\nChoose question type:",
                parse_mode="Markdown",
                reply_markup=test_types_keyboard(cat),
            )
        return True

    if data.startswith("ttype:"):
        # ttype:mcq:placement → ask difficulty
        parts = data.split(":")
        if len(parts) != 3:
            await _answer(query)
            return True
        _, ttype, cat = parts
        if cat not in CATEGORIES:
            await _answer(query, "Unknown category")
            return True
        await _answer(query)
        await query.edit_message_text(
            f"Difficulty for {ttype.upper()} — {category_label(cat)}:",
            reply_markup=difficulty_keyboard(cat, ttype),
        )
        return True

    if data.startswith("diff:"):
        # diff:easy:mcq:placement
        parts = data.split(":")
        if len(parts) != 4:
            await _answer(query)
            return True
        _, difficulty, ttype, cat = parts
        if cat not in CATEGORIES:
            await _answer(query, "Unknown category")
            return True
        study_state.begin_test(uid, cat, ttype, difficulty=difficulty)
        await _answer(query, "Generating…")
        await _send_test_question(update, context)
        return True

    if data == "test:next":
        sess = study_state.get(uid)
        if not sess.category or not sess.test_type or sess.mode != "test":
            await _answer(query, "Start a test first", show_alert=True)
            await study_home(update, context)
            return True
        if sess.awaiting_answer:
            await _answer(
                query,
                "Answer this question first (or End test).",
                show_alert=True,
            )
            return True
        await _answer(query)
        await _send_test_question(update, context)
        return True

    if data == "test:change_type":
        sess = study_state.get(uid)
        if not sess.category:
            await study_home(update, context)
            return True
        sess.awaiting_answer = False
        sess.current_question = None
        await _answer(query)
        await query.edit_message_text(
            f"📝 Test — {category_label(sess.category)}\nChoose question type:",
            reply_markup=test_types_keyboard(sess.category),
        )
        return True

    if data == "test:end":
        sess = study_state.get(uid)
        report = format_session_report(sess, user_id=uid)
        stats = progress_store.format_stats(uid)
        was_mock = sess.mode == "mock"
        study_state.reset_mode(uid)
        study_state.reset_score(uid)
        await _answer(query)
        title = "Mock exam complete" if was_mock else "Session ended"
        await query.edit_message_text(_clip(f"{title}\n\n{report}\n\n{stats}"))
        await _reply(
            update,
            "Copy the report above to share on LinkedIn or WhatsApp.",
            reply_markup=categories_keyboard(),
        )
        return True

    if data == "revise:flip":
        await _answer(query)
        await _revise_flip(update, context)
        return True

    if data == "revise:next":
        await _answer(query)
        await _revise_next(update, context)
        return True

    if data == "revise:mistakes":
        await _answer(query)
        sess = study_state.get(uid)
        if sess.category:
            await _start_revise(update, context, sess.category, from_mistakes=True)
        return True

    if data.startswith("upload_cat:"):
        cat = data.split(":", 1)[1]
        if cat not in CATEGORIES:
            await _answer(query, "Unknown")
            return True
        sess = study_state.get(uid)
        sess.category = cat
        sess.mode = None
        sess.awaiting_answer = False
        sess.awaiting_upload_category = True
        await _answer(query)
        await query.edit_message_text(
            f"Send a PDF, .txt, or .md file for *{category_label(cat)}*.\n"
            "It will be saved and indexed for Learn/Test.",
            parse_mode="Markdown",
        )
        return True

    if data == "upload:cancel":
        sess = study_state.get(uid)
        sess.awaiting_upload_category = False
        await _answer(query, "Cancelled")
        await query.edit_message_text("Upload cancelled.")
        return True

    return False


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(
        update,
        progress_store.format_stats(_uid(update)),
        reply_markup=main_reply_keyboard(),
    )


async def show_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    items = progress_store.list_wrong(uid, limit=5)
    if not items:
        await _reply(
            update,
            "No saved mistakes yet. Wrong test answers are stored for review.\n"
            "After a few mistakes, use 🎯 Practice mistakes.",
            reply_markup=main_reply_keyboard(),
        )
        return
    lines = ["📝 Recent mistakes (newest last):\n"]
    for i, w in enumerate(items, 1):
        lines.append(
            f"{i}. [{w.get('category')}/{w.get('qtype')}]\n"
            f"Q: {w.get('prompt')}\n"
            f"Hint: {w.get('explanation') or '—'}\n"
        )
    lines.append("Use Study → category → 🎯 Practice mistakes to drill these.")
    await _reply(update, "\n".join(lines), reply_markup=main_reply_keyboard())


async def _start_mistakes_practice(
    update: Update, context: ContextTypes.DEFAULT_TYPE, cat: str
) -> None:
    uid = _uid(update)
    wrongs = [
        w
        for w in progress_store.list_wrong(uid, limit=30)
        if w.get("category") == cat or True  # allow cross-category drill
    ]
    # Prefer same category; fall back to any
    same = [w for w in wrongs if w.get("category") == cat]
    pool = same or wrongs
    if not pool:
        await _reply(
            update,
            "No mistakes saved yet for practice. Take a Test first, then come back.",
            reply_markup=mode_keyboard(cat),
        )
        return
    study_state.begin_test(uid, cat, "theory", mode="mistakes", difficulty="medium")
    sess = study_state.get(uid)
    item = pool[-1]
    # Convert stored mistake into a revisit question
    q = {
        "type": "theory",
        "prompt": (
            "Revisit this missed question — answer again carefully:\n\n"
            f"{item.get('prompt')}"
        ),
        "options": None,
        "correct": item.get("explanation") or "See notes",
        "explanation": item.get("explanation") or "",
    }
    sess.current_question = q
    sess.awaiting_answer = True
    sess.test_type = "theory"
    sess.history.append(str(item.get("prompt") or "")[:200])
    await _reply(
        update,
        f"🎯 Mistakes practice ({len(pool)} saved)\n\n{q['prompt']}\n\n"
        "Write your improved answer.",
        reply_markup=test_controls_keyboard(),
    )


async def _speak_last_learn(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)
    text = (sess.last_learn_answer or "").strip()
    if not text:
        await _reply(update, "Ask a Learn question first, then tap 🔊 Hear answer.")
        return
    if not sarvam_service.is_configured():
        await _reply(update, "Voice needs SARVAM_API_KEY (already used for interview).")
        return
    await _reply(update, "🔊 Preparing audio…")
    try:
        audio = await sarvam_service.text_to_speech(text[:1500])
    except Exception as exc:  # noqa: BLE001
        logger.exception("learn TTS failed: %s", exc)
        await _reply(update, f"Voice failed: {exc}")
        return
    if not audio:
        await _reply(update, "Could not generate audio right now.")
        return
    chat = update.effective_chat
    if not chat:
        return
    from io import BytesIO

    bio = BytesIO(audio)
    bio.name = "learn.ogg"
    await context.bot.send_voice(chat_id=chat.id, voice=bio)


async def _send_test_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)
    if not sess.category or not sess.test_type:
        await _reply(update, "Pick a category and test type first.", reply_markup=categories_keyboard())
        return

    if sess.mode == "mistakes":
        await _start_mistakes_practice(update, context, sess.category)
        return

    if not rate_limit.allow(f"test:{uid}", max_hits=20, window_sec=60):
        wait = rate_limit.retry_after(f"test:{uid}", window_sec=60)
        await _reply(update, f"Slow down — try again in ~{wait}s.")
        return

    await _typing(update, context)
    await _reply(
        update,
        f"⏳ Preparing {sess.difficulty} {sess.test_type.upper()} "
        f"({topic_label(sess.category, sess.topic)})…",
    )
    hint = {
        "mcq": "important multiple choice concept",
        "msq": "topic with multiple correct points",
        "numerical": "formula or numerical problem",
        "theory": "explain a core concept",
    }.get(sess.test_type, "core syllabus topic")
    if sess.topic:
        hint = f"{topic_label(sess.category, sess.topic)} {hint}"

    try:
        ctx = await asyncio.to_thread(pipeline.context_for_topic, sess.category, hint)
        if not ctx.strip():
            await _reply(
                update,
                "⚠️ No indexed material for this topic yet — generating from your syllabus seeds.",
            )
        q = await asyncio.to_thread(
            groq_service.generate_test_question,
            category_label(sess.category),
            sess.test_type,
            ctx,
            sess.history,
            sess.difficulty,
        )
        progress_store.record_ai_call(uid)
    except Exception as exc:
        logger.exception("test question failed, forcing local: %s", exc)
        try:
            ctx = await asyncio.to_thread(
                pipeline.context_for_topic, sess.category or "placement", hint
            )
        except Exception:  # noqa: BLE001
            ctx = ""
        q = groq_service.generate_test_question(
            category_label(sess.category or "placement"),
            sess.test_type or "mcq",
            ctx,
            sess.history,
            sess.difficulty,
        )

    if not q or not q.get("prompt"):
        await _reply(update, "Could not build a question. Try another category or /reindex.")
        return

    sess.current_question = q
    sess.awaiting_answer = True
    prompt = q.get("prompt", "")
    sess.history.append(prompt[:200])

    lines = [
        f"{sess.test_type.upper()} · {sess.difficulty} · "
        f"{topic_label(sess.category, sess.topic)}\n{prompt}"
    ]
    options = q.get("options") or []
    if options:
        lines.append("")
        lines.extend(str(o) for o in options)

    if sess.test_type == "msq":
        lines.append("\nReply with letters, e.g. A C or AC.")
    elif sess.test_type == "mcq":
        lines.append("\nReply with one letter: A / B / C / D.")
    elif sess.test_type == "numerical":
        lines.append("\nReply with the numeric answer.")
    else:
        lines.append("\nWrite your answer in a few sentences.")

    plain = "\n".join(lines).replace("*", "")
    warn = progress_store.ai_usage_warning(uid)
    if warn:
        plain = f"{warn}\n\n{plain}"
    if groq_service.chat_used_offline():
        plain = _maybe_offline_notice(sess, plain)
    timer = _mock_timer_line(sess)
    if timer:
        plain = timer + plain
    kb = mock_controls_keyboard() if sess.mode == "mock" else test_controls_keyboard()
    await _reply(
        update,
        plain,
        reply_markup=kb,
    )


async def handle_study_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Route Learn answers / Test answers. Returns True if consumed."""
    uid = _uid(update)
    sess = study_state.get(uid)

    if sess.awaiting_upload_category:
        await _reply(
            update,
            "Waiting for a file upload (PDF / .txt / .md).\n"
            "Or tap Upload again to pick another category.",
            reply_markup=upload_category_keyboard(),
        )
        return True

    if sess.mode in {"test", "mistakes", "mock"} and sess.awaiting_answer and sess.current_question:
        await _grade_and_reply(update, context, text)
        return True

    if sess.mode in {"test", "mistakes", "mock"} and not sess.awaiting_answer:
        await _reply(
            update,
            "Tap Next question for another, or End + report.",
            reply_markup=mock_controls_keyboard() if sess.mode == "mock" else test_controls_keyboard(),
        )
        return True

    if sess.mode == "learn" and sess.category:
        await _learn_answer(update, context, text)
        return True

    return False


async def _learn_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)

    guard = guardrails.check_input(text)
    if guard.action != guardrails.GuardAction.ALLOW:
        await _reply(update, guard.message, reply_markup=learn_controls_keyboard())
        return

    if not rate_limit.allow(f"learn:{uid}", max_hits=15, window_sec=60):
        wait = rate_limit.retry_after(f"learn:{uid}", window_sec=60)
        await _reply(update, f"Too many questions — wait ~{wait}s.")
        return
    await _typing(update, context)
    await _reply(update, "⏳ Searching materials…")
    question = text
    if sess.topic:
        question = f"{topic_label(sess.category or '', sess.topic)}: {text}"
    lang = language.detect_language(text)
    try:
        answer = await asyncio.to_thread(
            _learn_with_lang,
            sess.category or "placement",
            question,
            lang,
        )
    except Exception as exc:
        logger.exception("learn answer failed: %s", exc)
        await _reply(update, f"Error: {exc}")
        return
    progress_store.record_ai_call(uid)
    answer = guardrails.sanitize_output(answer)
    answer = _maybe_offline_notice(sess, answer)
    warn = progress_store.ai_usage_warning(uid)
    if warn:
        answer = f"{warn}\n\n{answer}"
    sess.last_learn_answer = answer
    await _reply(update, answer, reply_markup=learn_controls_keyboard())
    # Optional auto voice for shorter answers
    if sess.voice_learn and sarvam_service.is_configured() and len(answer) < 900:
        try:
            audio = await sarvam_service.text_to_speech(answer[:1200])
            if audio and update.effective_chat:
                from io import BytesIO

                bio = BytesIO(audio)
                bio.name = "learn.ogg"
                await context.bot.send_voice(chat_id=update.effective_chat.id, voice=bio)
        except Exception as exc:  # noqa: BLE001
            logger.info("auto voice skipped: %s", exc)


def _theory_counts_correct(result: str) -> bool:
    m = re.search(r"Score:\s*(\d+)", result, re.I)
    return bool(m and int(m.group(1)) >= 7)


def _is_correct_result(qtype: str, result: str) -> bool:
    if result.strip().startswith("✅"):
        return True
    if qtype == "theory" and _theory_counts_correct(result):
        return True
    return False


async def _grade_and_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)
    q = sess.current_question or {}
    qtype = sess.test_type or q.get("type") or "theory"
    await _typing(update, context)
    try:
        result = await asyncio.to_thread(
            groq_service.grade_test_answer,
            category_label(sess.category or ""),
            qtype,
            q,
            text,
        )
    except Exception as exc:
        logger.exception("grade failed: %s", exc)
        await _reply(update, f"Grading error: {exc}")
        return

    # Numerical parse failure — let them retry without scoring
    if qtype == "numerical" and "Could not parse a number" in result:
        await _reply(
            update,
            f"{result}\n\nStill waiting for your numeric answer.",
            reply_markup=test_controls_keyboard(),
        )
        return

    sess.awaiting_answer = False
    sess.score_total += 1
    ok = _is_correct_result(qtype, result)
    if ok:
        sess.score_correct += 1

    sess.session_log.append(
        {
            "correct": ok,
            "qtype": qtype,
            "prompt": str(q.get("prompt") or "")[:300],
            "topic": sess.topic,
        }
    )

    progress_store.record_answer(
        uid,
        category=sess.category or "unknown",
        qtype=qtype,
        correct=ok,
        prompt=str(q.get("prompt") or ""),
        explanation=str(q.get("explanation") or result[:200]),
        student_answer=text,
    )

    # If mistakes mode and answered, drop one from stored wrongs when correct
    if sess.mode == "mistakes" and ok:
        progress_store.pop_wrong(uid, n=1)

    stats = progress_store.get_stats(uid)
    goal = int(stats.get("daily_goal") or 10)
    daily = int(stats.get("daily_answered") or 0)
    kb = mock_controls_keyboard() if sess.mode == "mock" else test_controls_keyboard()
    await _reply(
        update,
        f"{result}\n\n"
        f"Session: {sess.score_correct}/{sess.score_total} · "
        f"Today: {daily}/{goal} · Streak: {stats.get('streak', 0)}🔥",
        reply_markup=kb,
    )

    # Auto-end mock when target reached
    if sess.mode == "mock" and sess.mock_target and sess.score_total >= sess.mock_target:
        report = format_session_report(sess, user_id=uid)
        study_state.reset_mode(uid)
        study_state.reset_score(uid)
        await _reply(
            update,
            f"⏱ Mock exam finished!\n\n{report}",
            reply_markup=categories_keyboard(),
        )


def _learn_with_lang(category: str, question: str, lang: str) -> str:
    """RAG answer with optional Sarvam for Indic languages."""
    from config import category_label as cat_label

    hits = pipeline.search(category, question)
    if not hits:
        return (
            "I couldn't find relevant material for this category yet.\n"
            "• Tap Upload → pick category → send a PDF\n"
            "• Or ask the owner to add files under data/materials/ and run /reindex"
        )
    context_blocks = []
    sources: list[str] = []
    for h in hits:
        src = h["source"]
        if src not in sources:
            sources.append(src)
        context_blocks.append(f"[{src}]\n{h['text']}")
    context = "\n\n---\n\n".join(context_blocks)
    answer = groq_service.study_chat_answer(
        cat_label(category), question, context, lang
    )
    cited = ", ".join(sources[:4])
    return f"{answer}\n\n📄 Source: {cited}"


async def _start_revise(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    cat: str,
    *,
    from_mistakes: bool = False,
) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)
    sess.mode = "revise"
    sess.category = cat
    cards: list[dict] = []

    if from_mistakes:
        wrongs = progress_store.list_wrong(uid, limit=10)
        for w in reversed(wrongs):
            if w.get("category") == cat:
                cards.append(
                    {
                        "front": f"❓ {w.get('prompt', '')[:280]}",
                        "back": f"💡 {w.get('explanation') or 'Review your notes'}",
                        "source": "mistake",
                    }
                )

    if not cards:
        hint = topic_label(cat, sess.topic) if sess.topic else "key concepts"
        try:
            hits = await asyncio.to_thread(pipeline.search, cat, hint, 6)
            for h in hits[:6]:
                text = h["text"].strip()
                sentence = text[:200] + ("…" if len(text) > 200 else "")
                cards.append(
                    {
                        "front": f"🃏 What do you know about:\n{sentence}",
                        "back": text[:500],
                        "source": h["source"],
                    }
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("revise RAG failed: %s", exc)

    if not cards:
        await _reply(
            update,
            "No flashcards yet — upload materials or take a Test first.",
            reply_markup=mode_keyboard(cat),
        )
        sess.mode = None
        return

    sess.revise_cards = cards
    sess.revise_index = 0
    card = cards[0]
    src = card.get("source", "")
    src_line = f"\n📄 {src}" if src and src != "mistake" else ""
    await _reply(
        update,
        f"🃏 Quick revise — {category_label(cat)} (1/{len(cards)})\n\n"
        f"{card['front']}{src_line}\n\nTap *Show answer* when ready.",
        parse_mode="Markdown",
        reply_markup=revise_controls_keyboard(),
    )


async def _revise_flip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sess = study_state.get(_uid(update))
    if not sess.revise_cards or sess.mode != "revise":
        await _reply(update, "Start revise from Study → Quick revise.")
        return
    card = sess.revise_cards[sess.revise_index]
    src = card.get("source", "")
    src_line = f"\n📄 Source: {src}" if src and src != "mistake" else ""
    await _reply(
        update,
        f"🃏 Card {sess.revise_index + 1}/{len(sess.revise_cards)}\n\n"
        f"{card['back']}{src_line}",
        reply_markup=revise_controls_keyboard(),
    )


async def _revise_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sess = study_state.get(_uid(update))
    if not sess.revise_cards or sess.mode != "revise":
        await _reply(update, "Start revise from Study → Quick revise.")
        return
    sess.revise_index = (sess.revise_index + 1) % len(sess.revise_cards)
    card = sess.revise_cards[sess.revise_index]
    src = card.get("source", "")
    src_line = f"\n📄 {src}" if src and src != "mistake" else ""
    await _reply(
        update,
        f"🃏 Card {sess.revise_index + 1}/{len(sess.revise_cards)}\n\n"
        f"{card['front']}{src_line}\n\nTap *Show answer* when ready.",
        parse_mode="Markdown",
        reply_markup=revise_controls_keyboard(),
    )


async def upload_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sess = study_state.get(_uid(update))
    sess.awaiting_upload_category = False
    sess.mode = None
    sess.awaiting_answer = False
    await _reply(
        update,
        "📤 Upload study material\nChoose which category this file belongs to:",
        reply_markup=upload_category_keyboard(),
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.document:
        return
    uid = _uid(update)
    if not rate_limit.allow(f"upload:{uid}", max_hits=5, window_sec=3600):
        wait = rate_limit.retry_after(f"upload:{uid}", window_sec=3600)
        await update.message.reply_text(
            f"Upload limit reached — try again in ~{wait // 60} min."
        )
        return
    sess = study_state.get(uid)
    doc = update.message.document
    name = doc.file_name or "upload.bin"
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        await update.message.reply_text("Please upload a PDF, .txt, or .md file.")
        return

    cat = sess.category
    if not cat or cat not in CATEGORIES:
        await update.message.reply_text(
            "Pick a category for this upload first:",
            reply_markup=upload_category_keyboard(),
        )
        return

    dest_dir = uploads_path(uid, cat)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.\- ]+", "_", name)[:180] or f"upload{ext}"
    dest = dest_dir / safe_name

    tg_file = await context.bot.get_file(doc.file_id)
    await tg_file.download_to_drive(custom_path=str(dest))

    await update.message.reply_text(
        f"Saved {safe_name} → {category_label(cat)}. Indexing…"
    )
    try:
        stats = await asyncio.to_thread(pipeline.reindex_category, cat)
        await update.message.reply_text(
            f"✅ Indexed {category_label(cat)}: {stats['chunks']} chunks from {stats['files']} files.\n"
            "Ask a Learn question or start a Test.",
            reply_markup=mode_keyboard(cat),
        )
    except Exception as exc:
        logger.exception("reindex after upload failed: %s", exc)
        await update.message.reply_text(
            f"Saved, but indexing failed: {exc}\nTry /reindex later."
        )
    sess.awaiting_upload_category = False
    sess.mode = None


async def reindex_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "🔄 Rebuilding vector index for all categories… this may take a minute."
        )
    try:
        summary = await asyncio.to_thread(pipeline.reindex_all)
        lines = ["Done:"]
        for cat, stats in summary.items():
            lines.append(
                f"• {category_label(cat)}: {stats['chunks']} chunks ({stats['files']} files)"
            )
        await _reply(update, "\n".join(lines), reply_markup=main_reply_keyboard())
    except Exception as exc:
        logger.exception("reindex failed: %s", exc)
        await _reply(update, f"Reindex failed: {exc}")
