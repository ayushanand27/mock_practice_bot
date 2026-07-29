"""Mock interview practice using Groq (+ optional Sarvam voice)."""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from keyboards import interview_controls_keyboard, interview_topics_keyboard, main_reply_keyboard
from services import groq_service, sarvam_service
from services import interview_state as state

logger = logging.getLogger(__name__)


async def _answer(query, *args, **kwargs) -> None:
    try:
        await query.answer(*args, **kwargs)
    except BadRequest as exc:
        if "too old" in str(exc).lower() or "query id is invalid" in str(exc).lower():
            return
        raise


async def interview_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not groq_service.is_configured():
        await update.message.reply_text(
            "Mock interviews need GROQ_API_KEY in .env.\n"
            "Add your key from https://console.groq.com and restart the bot."
        )
        return
    await update.message.reply_text(
        "Mock interview — pick a topic:",
        reply_markup=interview_topics_keyboard(),
    )


async def interview_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await interview_command(update, context)


async def end_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    session = state.end_session(update.effective_user.id)
    if not session:
        await update.message.reply_text("No active interview. Start with /interview.")
        return
    await update.message.reply_text(
        f"Interview ended ({session.topic}). You answered {len(session.history)} question(s).\n"
        "Start again anytime with /interview.",
        reply_markup=main_reply_keyboard(),
    )


async def _ask_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    session = state.get_session(user_id)
    if not session or not session.active:
        return

    session.question_num += 1
    try:
        question = await asyncio.to_thread(
            groq_service.generate_question,
            session.topic,
            session.question_num,
            session.history,
        )
    except Exception as exc:
        logger.exception("Question generation failed")
        text = f"Could not generate a question ({exc}). Try /end and start again."
        if update.callback_query:
            await update.callback_query.message.reply_text(text)
        elif update.message:
            await update.message.reply_text(text)
        return

    session.current_question = question
    header = f"Question {session.question_num} ({session.topic}):\n\n{question}"
    target = update.callback_query.message if update.callback_query else update.message
    if not target:
        return

    await target.reply_text(header, reply_markup=interview_controls_keyboard())

    # Optional Sarvam TTS — degrade gracefully if missing/fails
    audio = await sarvam_service.text_to_speech(question)
    if audio:
        await target.reply_voice(voice=BytesIO(audio), caption="Spoken question (Sarvam TTS)")


async def topic_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await _answer(query)
    data = query.data or ""

    if data == "interview:cancel":
        await query.edit_message_text("Interview cancelled.")
        return
    if data == "interview:end":
        session = state.end_session(update.effective_user.id)
        n = len(session.history) if session else 0
        await query.edit_message_text(f"Interview ended. Answered {n} question(s).")
        return
    if data == "interview:next":
        if not state.is_active(update.effective_user.id):
            await _answer(query, "No active interview — use /interview", show_alert=True)
            return
        await _ask_next_question(update, context, update.effective_user.id)
        return
    if data.startswith("topic:"):
        if not groq_service.is_configured():
            await query.edit_message_text("GROQ_API_KEY is missing. Add it to .env and restart.")
            return
        topic = data.split(":", 1)[1]
        state.start_session(update.effective_user.id, topic)
        await query.edit_message_text(
            f"Starting {topic} mock interview.\n"
            "Reply with your answer (text or voice). /end to stop."
        )
        await _ask_next_question(update, context, update.effective_user.id)


async def handle_interview_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, answer: str) -> bool:
    """Evaluate an answer if user is in an interview. Returns True if handled."""
    if not update.effective_user or not update.message:
        return False
    session = state.get_session(update.effective_user.id)
    if not session or not session.active or not session.current_question:
        return False

    answer = (answer or "").strip()
    if not answer:
        await update.message.reply_text("I didn't catch an answer. Try again or /end.")
        return True

    await update.message.reply_chat_action("typing")
    try:
        feedback = await asyncio.to_thread(
            groq_service.evaluate_answer,
            session.topic,
            session.current_question,
            answer,
        )
    except Exception as exc:
        await update.message.reply_text(f"Could not evaluate answer: {exc}")
        return True

    session.history.append(
        {
            "question": session.current_question,
            "answer": answer,
            "feedback": feedback,
        }
    )
    await update.message.reply_text(
        f"{feedback}\n\nSend another answer after the next question, or tap Next / /end.",
        reply_markup=interview_controls_keyboard(),
    )
    # Auto-advance to next question
    await _ask_next_question(update, context, update.effective_user.id)
    return True


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not state.is_active(update.effective_user.id):
        await update.message.reply_text(
            "Voice notes are used during mock interviews.\nStart with /interview."
        )
        return
    if not sarvam_service.is_configured():
        await update.message.reply_text(
            "SARVAM_API_KEY is not set, so I can't transcribe voice yet.\n"
            "Please type your answer, or add a Sarvam key and restart."
        )
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return
    await update.message.reply_chat_action("typing")
    tg_file = await context.bot.get_file(voice.file_id)
    buf = BytesIO()
    await tg_file.download_to_memory(buf)
    audio_bytes = buf.getvalue()
    transcript = await sarvam_service.speech_to_text(audio_bytes, filename="voice.ogg")
    if not transcript:
        await update.message.reply_text(
            "Couldn't transcribe that voice note. Please type your answer."
        )
        return
    await update.message.reply_text(f"Heard: {transcript}")
    await handle_interview_answer(update, context, transcript)
