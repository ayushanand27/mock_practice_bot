"""Study flow: category → Learn / Test, RAG Q&A, tests, upload, reindex."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config import CATEGORIES, SUPPORTED_EXTS, category_label, uploads_path
from keyboards import (
    categories_keyboard,
    learn_controls_keyboard,
    main_reply_keyboard,
    mode_keyboard,
    test_controls_keyboard,
    test_types_keyboard,
    upload_category_keyboard,
)
from rag import pipeline
from services import groq_service, study_state

logger = logging.getLogger(__name__)


async def _answer(query, text: str | None = None, **kwargs) -> None:
    try:
        if text is None:
            await _answer(query, **kwargs)
        else:
            await _answer(query, text, **kwargs)
    except BadRequest as exc:
        msg = str(exc).lower()
        if "too old" in msg or "query id is invalid" in msg:
            logger.info("Ignoring stale callback: %s", exc)
            return
        raise


def _uid(update: Update) -> int:
    user = update.effective_user
    return user.id if user else 0


async def _reply(update: Update, text: str, **kwargs) -> None:
    if update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(text, **kwargs)
    elif update.message:
        await update.message.reply_text(text, **kwargs)


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
            f"📝 Test — {category_label(sess.category)}\nChoose question type:",
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
            f"Category: *{category_label(cat)}*\n\nLearn from docs, or take a test.",
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
                "Ask any concept question. I'll answer from your study materials.\n"
                "Examples: \"What is ACID?\", \"Explain projectile motion\".\n"
                "Send /study to switch category.",
                parse_mode="Markdown",
                reply_markup=learn_controls_keyboard(),
            )
        else:
            await query.edit_message_text(
                f"📝 *Test — {category_label(cat)}*\nChoose question type:",
                parse_mode="Markdown",
                reply_markup=test_types_keyboard(cat),
            )
        return True

    if data.startswith("ttype:"):
        # ttype:mcq:placement
        parts = data.split(":")
        if len(parts) != 3:
            await _answer(query)
            return True
        _, ttype, cat = parts
        if cat not in CATEGORIES:
            await _answer(query, "Unknown category")
            return True
        study_state.begin_test(uid, cat, ttype)
        await _answer(query, "Generating…")
        await _send_test_question(update, context)
        return True

    if data == "test:next":
        sess = study_state.get(uid)
        if not sess.category or not sess.test_type or sess.mode != "test":
            await _answer(query, "Start a test first", show_alert=True)
            await study_home(update, context)
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
            f"Test ended.\nScore: {sess.score_correct}/{sess.score_total}"
            if sess.score_total
            else "Test ended — no questions answered."
        )
        study_state.reset_mode(uid)
        study_state.reset_score(uid)
        await _answer(query)
        await query.edit_message_text(summary)
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


async def _send_test_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)
    if not sess.category or not sess.test_type:
        await _reply(update, "Pick a category and test type first.", reply_markup=categories_keyboard())
        return

    if not groq_service.is_configured():
        await _reply(update, "GROQ_API_KEY is missing — cannot generate tests.")
        return

    await _reply(update, "⏳ Preparing question…")
    hint = {
        "mcq": "important multiple choice concept",
        "msq": "topic with multiple correct points",
        "numerical": "formula or numerical problem",
        "theory": "explain a core concept",
    }.get(sess.test_type, "core syllabus topic")

    try:
        ctx = await asyncio.to_thread(pipeline.context_for_topic, sess.category, hint)
        q = await asyncio.to_thread(
            groq_service.generate_test_question,
            category_label(sess.category),
            sess.test_type,
            ctx,
            sess.history,
        )
    except Exception as exc:
        logger.exception("test question failed: %s", exc)
        await _reply(update, f"Could not generate question: {exc}")
        return

    sess.current_question = q
    sess.awaiting_answer = True
    prompt = q.get("prompt", "")
    sess.history.append(prompt[:200])

    lines = [f"{sess.test_type.upper()}\n{prompt}"]
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
        await _grade_and_reply(update, text)
        return True

    if sess.mode == "learn" and sess.category:
        await _learn_answer(update, text)
        return True

    return False


async def _learn_answer(update: Update, text: str) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)
    if not groq_service.is_configured():
        await _reply(update, "GROQ_API_KEY is missing — cannot answer.")
        return
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
    # Telegram message limit ~4096
    if len(answer) > 4000:
        answer = answer[:3990] + "…"
    await _reply(update, answer, reply_markup=learn_controls_keyboard())


def _theory_counts_correct(result: str) -> bool:
    m = re.search(r"Score:\s*(\d+)", result, re.I)
    return bool(m and int(m.group(1)) >= 7)


async def _grade_and_reply(update: Update, text: str) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)
    q = sess.current_question or {}
    qtype = sess.test_type or q.get("type") or "theory"
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

    sess.awaiting_answer = False
    sess.score_total += 1
    if result.strip().startswith("✅"):
        sess.score_correct += 1
    elif qtype == "theory" and _theory_counts_correct(result):
        sess.score_correct += 1

    await _reply(
        update,
        f"{result}\n\nRunning score: {sess.score_correct}/{sess.score_total}",
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
