"""Study flow: category → Learn / Test, RAG Q&A, tests, upload, reindex."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import CATEGORIES, SUPPORTED_EXTS, category_label, uploads_path
from keyboards import (
    categories_keyboard,
    difficulty_keyboard,
    learn_controls_keyboard,
    main_reply_keyboard,
    mode_keyboard,
    test_controls_keyboard,
    test_types_keyboard,
    upload_category_keyboard,
)
from rag import pipeline
from rag import store as rag_store
from services import groq_service, progress_store, rate_limit, study_state

logger = logging.getLogger(__name__)

TG_MAX = 3900


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
        return f"Indexed chunks: {n}."
    return (
        "⚠️ Few/no materials indexed yet — answers may be weaker. "
        "Upload a PDF or add files under data/materials, then /reindex."
    )


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
        sess.mode = None
        sess.awaiting_answer = False
        sess.awaiting_upload_category = False
        await _answer(query)
        await query.edit_message_text(
            f"Category: *{category_label(cat)}*\n\n"
            f"{_empty_materials_note(cat)}\n\n"
            "Learn from docs, or take a test.",
            reply_markup=mode_keyboard(cat),
            parse_mode="Markdown",
        )
        return True

    if data.startswith("mode:"):
        # mode:learn:placement | mode:test:placement
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
        sess.mode = mode
        sess.awaiting_answer = False
        sess.current_question = None
        sess.awaiting_upload_category = False
        await _answer(query)
        if mode == "learn":
            await query.edit_message_text(
                f"📖 *Learn — {category_label(cat)}*\n\n"
                f"{_empty_materials_note(cat)}\n\n"
                "Ask any concept question. I'll answer from your study materials "
                "and cite sources.\n"
                "Examples: \"What is ACID?\", \"Explain projectile motion\".\n"
                "Send /study to switch category.",
                parse_mode="Markdown",
                reply_markup=learn_controls_keyboard(),
            )
        else:
            await query.edit_message_text(
                f"📝 *Test — {category_label(cat)}*\n"
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
        summary = (
            f"Test ended.\nSession score: {sess.score_correct}/{sess.score_total}\n\n"
            f"{progress_store.format_stats(uid)}"
            if sess.score_total
            else "Test ended — no questions answered."
        )
        study_state.reset_mode(uid)
        study_state.reset_score(uid)
        await _answer(query)
        await query.edit_message_text(_clip(summary))
        await _reply(update, "Back to categories:", reply_markup=categories_keyboard())
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
            "No saved mistakes yet. Wrong test answers are stored for review.",
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
    await _reply(update, "\n".join(lines), reply_markup=main_reply_keyboard())


async def _send_test_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)
    if not sess.category or not sess.test_type:
        await _reply(update, "Pick a category and test type first.", reply_markup=categories_keyboard())
        return

    if not rate_limit.allow(f"test:{uid}", max_hits=20, window_sec=60):
        wait = rate_limit.retry_after(f"test:{uid}", window_sec=60)
        await _reply(update, f"Slow down — try again in ~{wait}s.")
        return

    await _typing(update, context)
    await _reply(
        update,
        f"⏳ Preparing {sess.difficulty} {sess.test_type.upper()} question…",
    )
    hint = {
        "mcq": "important multiple choice concept",
        "msq": "topic with multiple correct points",
        "numerical": "formula or numerical problem",
        "theory": "explain a core concept",
    }.get(sess.test_type, "core syllabus topic")

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

    lines = [f"{sess.test_type.upper()} · {sess.difficulty}\n{prompt}"]
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

    # Avoid Markdown — LLM prompts often contain _ * ` that break Telegram parse_mode
    plain = "\n".join(lines).replace("*", "")
    await _reply(
        update,
        plain,
        reply_markup=test_controls_keyboard(),
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

    if sess.mode == "test" and sess.awaiting_answer and sess.current_question:
        await _grade_and_reply(update, context, text)
        return True

    if sess.mode == "test" and not sess.awaiting_answer:
        await _reply(
            update,
            "Tap Next question for another, or End test.",
            reply_markup=test_controls_keyboard(),
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
    if not groq_service.is_configured():
        await _reply(update, "GROQ_API_KEY is missing — cannot answer.")
        return
    if not rate_limit.allow(f"learn:{uid}", max_hits=15, window_sec=60):
        wait = rate_limit.retry_after(f"learn:{uid}", window_sec=60)
        await _reply(update, f"Too many questions — wait ~{wait}s.")
        return
    await _typing(update, context)
    await _reply(update, "⏳ Searching materials…")
    try:
        answer = await asyncio.to_thread(
            pipeline.answer_question,
            sess.category or "placement",
            text,
        )
    except Exception as exc:
        logger.exception("learn answer failed: %s", exc)
        await _reply(update, f"Error: {exc}")
        return
    await _reply(update, answer, reply_markup=learn_controls_keyboard())


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

    progress_store.record_answer(
        uid,
        category=sess.category or "unknown",
        qtype=qtype,
        correct=ok,
        prompt=str(q.get("prompt") or ""),
        explanation=str(q.get("explanation") or result[:200]),
        student_answer=text,
    )

    stats = progress_store.get_stats(uid)
    goal = int(stats.get("daily_goal") or 10)
    daily = int(stats.get("daily_answered") or 0)
    await _reply(
        update,
        f"{result}\n\n"
        f"Session: {sess.score_correct}/{sess.score_total} · "
        f"Today: {daily}/{goal} · Streak: {stats.get('streak', 0)}🔥",
        reply_markup=test_controls_keyboard(),
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
