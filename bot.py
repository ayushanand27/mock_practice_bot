"""
RAG Study Telegram bot — Learn & Test from local materials.

Commands: /start /study /help /ping /reindex /note /notes /clearnotes
          /remind /reminders /interview /end
"""

from __future__ import annotations

import atexit
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.error import BadRequest, Conflict, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from handlers import interview, notes, reminders, study
from handlers.common import help_command, ping, send_main_menu, start
from services import interview_state as state

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_LOCK_PATH = Path(__file__).resolve().parent / "data" / "bot.lock"
_lock_fh = None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except SystemError:
        return False
    # On Windows, os.kill(pid, 0) may not work the same — use OpenProcess via ctypes fallback
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    return True


def acquire_singleton_lock() -> None:
    """Ensure only one polling bot runs for this project."""
    global _lock_fh
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _LOCK_PATH.exists():
        try:
            old = int(_LOCK_PATH.read_text(encoding="utf-8").strip().split()[0])
        except (ValueError, OSError, IndexError):
            old = 0
        if old and old != os.getpid() and _pid_alive(old):
            raise SystemExit(
                f"Another bot.py is already running (pid {old}). "
                "Stop it first, then start again."
            )
    _lock_fh = open(_LOCK_PATH, "w", encoding="utf-8")
    _lock_fh.write(f"{os.getpid()}\n")
    _lock_fh.flush()

    def _release() -> None:
        try:
            if _LOCK_PATH.exists():
                text = _LOCK_PATH.read_text(encoding="utf-8").strip()
                if text.startswith(str(os.getpid())):
                    _LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            if _lock_fh:
                _lock_fh.close()
        except OSError:
            pass

    atexit.register(_release)


async def post_init(app: Application) -> None:
    """Warm RAG index after bot starts (non-blocking-ish via thread risk — run sync)."""
    try:
        from rag import pipeline

        logger.info("Building / loading RAG index…")
        pipeline.get_index()
        logger.info("RAG index ready.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG index init failed (bot still runs): %s", exc)


async def menu_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    user = update.effective_user
    user_id = user.id if user else 0

    if text in {"Help", "help"}:
        await help_command(update, context)
        return
    if text in {"📚 Study", "Study", "/study"}:
        await study.study_home(update, context)
        return
    if text in {"Upload", "📤 Upload"}:
        await study.upload_prompt(update, context)
        return
    if text == "Notes":
        await notes.notes_menu(update, context)
        return
    if text == "Reminders":
        await reminders.reminders_menu(update, context)
        return
    if text == "Mock Practice":
        await interview.interview_menu(update, context)
        return

    if await reminders.try_natural_remind(update, context):
        return

    if await study.handle_study_text(update, context, text):
        return

    if state.is_active(user_id):
        await interview.handle_interview_answer(update, context, text)
        return

    await send_main_menu(
        update,
        "Use Study to Learn or Test, or /help for commands.",
    )


async def safe_answer(query, **kwargs) -> None:
    """Ignore stale callback queries (Telegram ~seconds timeout)."""
    try:
        await query.answer(**kwargs)
    except BadRequest as exc:
        if "too old" in str(exc).lower() or "query id is invalid" in str(exc).lower():
            logger.info("Ignoring stale callback: %s", exc)
            return
        raise


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    data = query.data
    if data == "noop":
        await safe_answer(query)
        return

    if await study.handle_callback(update, context):
        return

    if data.startswith("remind_cancel:"):
        await reminders.cancel_reminder_callback(update, context)
        return
    if data.startswith("topic:") or data.startswith("interview:"):
        await interview.topic_callback(update, context)
        return
    await safe_answer(query)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    exc = context.error
    if isinstance(exc, BadRequest) and (
        "too old" in str(exc).lower() or "query id is invalid" in str(exc).lower()
    ):
        logger.info("Stale callback ignored: %s", exc)
        return
    if isinstance(exc, (TimedOut, NetworkError)):
        logger.warning("Transient Telegram error: %s", exc)
        return
    # Conflict usually means a second polling process — log clearly, keep running
    if isinstance(exc, Conflict) or (exc is not None and "Conflict" in type(exc).__name__):
        logger.error(
            "Telegram Conflict (another bot.py is polling this token). "
            "Stop duplicate processes. Detail: %s",
            exc,
        )
        return
    logger.exception("Unhandled error while processing update: %s", exc)


def main() -> None:
    acquire_singleton_lock()
    token = os.getenv("BOT_TOKEN")
    if not token or token == "your_bot_token_here":
        raise SystemExit(
            "Missing BOT_TOKEN. Copy .env.example to .env and paste your token from @BotFather."
        )

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("study", study.study_command))
    app.add_handler(CommandHandler("reindex", study.reindex_command))

    app.add_handler(CommandHandler("note", notes.note_command))
    app.add_handler(CommandHandler("notes", notes.notes_command))
    app.add_handler(CommandHandler("clearnotes", notes.clearnotes_command))

    app.add_handler(CommandHandler("remind", reminders.remind_command))
    app.add_handler(CommandHandler("reminders", reminders.reminders_command))

    app.add_handler(CommandHandler("interview", interview.interview_command))
    app.add_handler(CommandHandler("end", interview.end_command))

    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.Document.ALL, study.handle_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, interview.handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text_router))
    app.add_error_handler(on_error)

    if app.job_queue is None:
        logger.warning(
            "JobQueue is None — install python-telegram-bot[job-queue] for reminders."
        )
    else:
        logger.info("JobQueue ready for reminders.")

    logger.info("Study bot starting… Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
