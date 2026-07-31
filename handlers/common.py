"""Shared help text and menu helpers — study-first UX."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from keyboards import categories_keyboard, main_reply_keyboard
from services import groq_service, progress_store, sarvam_service

HELP_TEXT = """*RAG Study Bot*

/start – pick a study category
/study – Learn or Test menu
/stats – streak, daily goal, accuracy
/review – revisit wrong answers
/reindex – rebuild vector index
/help · /ping

*Study flow*
1. Category: Placement · JEE · NEET · Class 11 · Class 12 · SSC CGL · UPSC
2. *Learn* – ask concepts (answers grounded in your docs + sources)
3. *Test* – MCQ / MSQ / Numerical / Theory → pick Easy/Medium/Hard → score

*Materials*
• Drop PDFs in data/materials/{{category}}/ then /reindex
• Or tap Upload → category → send PDF/txt/md

*Secondary*
/note · /notes · /clearnotes
/remind · /reminders
/interview · /end

APIs: Groq {groq} · Sarvam {sarvam}
"""


def help_message() -> str:
    return HELP_TEXT.format(
        groq="OK" if groq_service.is_configured() else "missing",
        sarvam="OK" if sarvam_service.is_configured() else "optional/off",
    )


async def send_main_menu(update: Update, text: str) -> None:
    if update.message:
        await update.message.reply_text(text, reply_markup=main_reply_keyboard())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user else "there"
    uid = user.id if user else 0
    streak_line = ""
    if uid:
        stats = progress_store.get_stats(uid)
        streak_line = (
            f"\n🔥 Streak: {stats.get('streak', 0)} · "
            f"Today {stats.get('daily_answered', 0)}/{stats.get('daily_goal', 10)}"
        )
    pitch = (
        f"Hi {name}! 👋{streak_line}\n\n"
        "*Your exam coach on Telegram* @mock_practice_bot\n\n"
        "📚 *What it does*\n"
        "Learn concepts and take practice tests grounded in *your* study materials "
        "(Placement, JEE, NEET, Class 11/12, SSC CGL, UPSC).\n\n"
        "🚀 *How to start*\n"
        "1. Pick a category below\n"
        "2. Pick a topic (or All topics)\n"
        "3. Tap *Learn* (ask anything) or *Test* (MCQ / MSQ / Numerical / Theory)\n"
        "4. End test → shareable session report\n\n"
        "📤 *Materials*\n"
        "Upload a PDF for best results (Upload → category → send file).\n"
        "No PDF yet? The bot still works on whatever notes are already indexed.\n\n"
        "ℹ️ *Free AI quota*\n"
        "Heavy use may switch to notes-only answers until the daily reset "
        "(~5:30 AM IST). Learn & Test keep working.\n\n"
        "Pick a category to begin:"
    )
    if update.message:
        await update.message.reply_text(
            pitch,
            reply_markup=categories_keyboard(),
            parse_mode="Markdown",
        )
        await update.message.reply_text(
            "Shortcuts: 📚 Study · Upload · 📊 Progress · 📝 Review",
            reply_markup=main_reply_keyboard(),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            help_message(),
            reply_markup=main_reply_keyboard(),
            parse_mode="Markdown",
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from handlers import study

    await study.show_stats(update, context)


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from handlers import study

    await study.show_review(update, context)


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("pong")
