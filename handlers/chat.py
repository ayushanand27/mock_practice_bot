"""Free-form study chat — RAG when category set, general help otherwise."""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import category_label, topic_label
from handlers.study import _clip, _maybe_offline_notice, _reply, _typing, _uid
from keyboards import chat_controls_keyboard, main_reply_keyboard
from rag import pipeline
from services import groq_service, guardrails, language, progress_store, rate_limit, study_state

logger = logging.getLogger(__name__)

CHAT_RATE = 12
CHAT_WINDOW = 60
FLOOD_RATE = 25
FLOOD_WINDOW = 30


def _chat_rate_key(uid: int) -> str:
    return f"chat:{uid}"


def _flood_key(uid: int) -> str:
    return f"flood:{uid}"


async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)
    sess.mode = "chat"
    sess.awaiting_answer = False
    sess.current_question = None
    focus = ""
    if sess.category:
        focus = f"\n📎 Context: {category_label(sess.category)}"
        if sess.topic:
            focus += f" · {topic_label(sess.category, sess.topic)}"
    await _reply(
        update,
        "💬 *Study chat*\n"
        "Ask anything — concepts, doubts, quick revision."
        f"{focus}\n\n"
        "With a category selected, answers use your uploaded materials.\n"
        "Type /study to return to menus.",
        parse_mode="Markdown",
        reply_markup=chat_controls_keyboard(),
    )


async def exit_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    sess = study_state.get(uid)
    if sess.mode == "chat":
        sess.mode = None
    await _reply(update, "Back to menus.", reply_markup=main_reply_keyboard())


async def handle_chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query or not query.data:
        return False
    data = query.data
    if data == "chat:start":
        await query.answer()
        await chat_command(update, context)
        return True
    if data == "chat:exit":
        await query.answer()
        await exit_chat(update, context)
        return True
    return False


async def handle_chat_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> bool:
    """Handle free chat. Returns True if consumed."""
    uid = _uid(update)
    sess = study_state.get(uid)

    in_chat = sess.mode == "chat"
    idle = sess.mode not in {"learn", "test", "mistakes", "mock", "revise"} and not sess.awaiting_upload_category
    if not in_chat and not idle:
        return False
    if sess.mode in {"learn", "test", "mistakes", "mock"} and (
        sess.awaiting_answer or sess.mode == "learn"
    ):
        return False

    guard = guardrails.check_input(text)
    if guard.action == guardrails.GuardAction.HARD_BLOCK:
        await _reply(update, guard.message, reply_markup=chat_controls_keyboard())
        return True
    if guard.action == guardrails.GuardAction.SOFT_BLOCK:
        await _reply(update, guard.message, reply_markup=chat_controls_keyboard())
        return True

    if not rate_limit.allow(_flood_key(uid), max_hits=FLOOD_RATE, window_sec=FLOOD_WINDOW):
        wait = rate_limit.retry_after(_flood_key(uid), window_sec=FLOOD_WINDOW)
        await _reply(
            update,
            f"⏳ Too many messages — cooldown ~{wait}s. I'm still here for study!",
            reply_markup=chat_controls_keyboard(),
        )
        return True

    if not rate_limit.allow(_chat_rate_key(uid), max_hits=CHAT_RATE, window_sec=CHAT_WINDOW):
        wait = rate_limit.retry_after(_chat_rate_key(uid), window_sec=CHAT_WINDOW)
        await _reply(
            update,
            f"Slow down — try again in ~{wait}s. (Study chat limit: {CHAT_RATE}/min)",
            reply_markup=chat_controls_keyboard(),
        )
        return True

    sess.mode = "chat"
    await _typing(update, context)
    await _reply(update, "⏳ Thinking…")

    lang = language.detect_language(text)
    answer = await _answer_chat(uid, sess, text, lang)
    answer = guardrails.sanitize_output(answer)
    answer = _maybe_offline_notice(sess, answer)
    warn = progress_store.ai_usage_warning(uid)
    if warn:
        answer = f"{warn}\n\n{answer}"
    sess.last_learn_answer = answer
    await _reply(update, _clip(answer), reply_markup=chat_controls_keyboard())
    return True


async def _answer_chat(
    uid: int, sess: study_state.StudySession, text: str, lang: str
) -> str:
    uid_cat = sess.category
    question = text
    if uid_cat and sess.topic:
        question = f"{topic_label(uid_cat, sess.topic)}: {text}"

    if uid_cat:
        try:
            hits = await asyncio.to_thread(pipeline.search, uid_cat, question, 4)
            if hits:
                blocks = []
                sources: list[str] = []
                for h in hits:
                    src = h["source"]
                    if src not in sources:
                        sources.append(src)
                    blocks.append(f"[{src}]\n{h['text']}")
                context = "\n\n---\n\n".join(blocks)
                answer = await asyncio.to_thread(
                    groq_service.study_chat_answer,
                    category_label(uid_cat),
                    question,
                    context,
                    lang,
                )
                progress_store.record_ai_call(uid)
                cited = ", ".join(sources[:3])
                return f"{answer}\n\n📄 Source: {cited}"
        except Exception as exc:
            logger.exception("RAG chat failed: %s", exc)

    answer = await asyncio.to_thread(
        groq_service.general_study_chat,
        question,
        category_label(uid_cat) if uid_cat else None,
        lang,
    )
    progress_store.record_ai_call(uid)
    return answer
