"""Reminder scheduling via JobQueue."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from handlers.common import send_main_menu
from keyboards import cancel_reminder_keyboard, reminders_list_keyboard

logger = logging.getLogger(__name__)

# /remind 10 drink water  OR  remind me in 10 min to drink water
_NATURAL = re.compile(
    r"(?i)^\s*remind\s+me\s+in\s+(\d+)\s*(min|mins|minute|minutes|m)?\s*(?:to\s+|[:\-]\s*)?(.+)$"
)


def _user_jobs(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> list:
    if not context.job_queue:
        return []
    prefix = f"remind_{user_id}_"
    return [j for j in context.job_queue.jobs() if j.name and j.name.startswith(prefix)]


async def _fire_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if not job:
        return
    data = job.data or {}
    chat_id = data.get("chat_id")
    message = data.get("message", "Reminder!")
    if chat_id is not None:
        await context.bot.send_message(chat_id=chat_id, text=f"Reminder: {message}")


def _schedule(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    minutes: int,
    message: str,
) -> str:
    if not context.job_queue:
        raise RuntimeError("JobQueue is not available. Install python-telegram-bot[job-queue].")
    if minutes < 1:
        raise ValueError("Minutes must be at least 1.")
    job_name = f"remind_{user_id}_{uuid.uuid4().hex[:8]}"
    context.job_queue.run_once(
        _fire_reminder,
        when=minutes * 60,
        data={
            "chat_id": chat_id,
            "user_id": user_id,
            "message": message,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "minutes": minutes,
        },
        name=job_name,
        chat_id=chat_id,
        user_id=user_id,
    )
    return job_name


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /remind <minutes> <message>\n"
            "Example: /remind 10 review system design notes\n"
            "Or: remind me in 10 min to stretch"
        )
        return
    try:
        minutes = int(context.args[0])
    except ValueError:
        await update.message.reply_text("First argument must be minutes (a number).")
        return
    message = " ".join(context.args[1:]).strip()
    if not message:
        await update.message.reply_text("Please include a reminder message.")
        return
    try:
        job_name = _schedule(
            context,
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
            minutes=minutes,
            message=message,
        )
    except Exception as exc:
        await update.message.reply_text(f"Could not set reminder: {exc}")
        return
    await update.message.reply_text(
        f"Got it — I'll remind you in {minutes} min:\n{message}",
        reply_markup=cancel_reminder_keyboard(job_name),
    )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    jobs = _user_jobs(context, update.effective_user.id)
    if not jobs:
        await update.message.reply_text("No pending reminders.")
        return
    lines = []
    names = []
    for i, job in enumerate(jobs, start=1):
        data = job.data or {}
        msg = data.get("message", "(no message)")
        mins = data.get("minutes", "?")
        lines.append(f"{i}. in ~{mins} min — {msg}")
        names.append(job.name)
    await update.message.reply_text(
        "Pending reminders:\n\n" + "\n".join(lines),
        reply_markup=reminders_list_keyboard(names),
    )


async def reminders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reminders_command(update, context)
    if update.message:
        await update.message.reply_text(
            "Set one with /remind <minutes> <message>\n"
            "or: remind me in 10 min to …"
        )


async def try_natural_remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Parse natural-language remind. Returns True if handled."""
    if not update.message or not update.message.text or not update.effective_user:
        return False
    match = _NATURAL.match(update.message.text.strip())
    if not match:
        return False
    minutes = int(match.group(1))
    message = (match.group(3) or "").strip()
    if not message:
        await update.message.reply_text("What should I remind you about?")
        return True
    try:
        job_name = _schedule(
            context,
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
            minutes=minutes,
            message=message,
        )
    except Exception as exc:
        await update.message.reply_text(f"Could not set reminder: {exc}")
        return True
    await update.message.reply_text(
        f"Got it — I'll remind you in {minutes} min:\n{message}",
        reply_markup=cancel_reminder_keyboard(job_name),
    )
    return True


async def cancel_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except BadRequest as exc:
        if "too old" not in str(exc).lower() and "query id is invalid" not in str(exc).lower():
            raise
    data = query.data or ""
    if not data.startswith("remind_cancel:"):
        return
    job_name = data.split(":", 1)[1]
    if not context.job_queue:
        await query.edit_message_text("Reminders unavailable (JobQueue missing).")
        return
    jobs = context.job_queue.get_jobs_by_name(job_name)
    if not jobs:
        await query.edit_message_text("That reminder is already gone.")
        return
    for job in jobs:
        job.schedule_removal()
    await query.edit_message_text("Reminder cancelled.")
