"""Shared help text and menu helpers — study-first UX."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from keyboards import categories_keyboard, main_reply_keyboard
from services import groq_service, sarvam_service

HELP_TEXT = """*RAG Study Bot*

/start – pick a study category
/study – Learn or Test menu
/reindex – rebuild vector index from materials + uploads
/help · /ping

*Study flow*
1. Category: Placement · JEE · NEET · Class 11 · Class 12 · SSC CGL · UPSC
2. *Learn* – ask e.g. "What is ACID?" (answers from your docs)
3. *Test* – MCQ, MSQ, Numerical, or Theory → answer → score

*Materials*
• Drop PDFs in data/materials/{{category}}/ then /reindex
• Or tap Upload → category → send PDF/txt/md (auto-reindexes)

*Secondary*
/note · /notes · /clearnotes
/remind · /reminders
/interview · /end (mock interview + optional voice)

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
    if update.message:
        await update.message.reply_text(
            f"Hi {name}! 👋\n\n"
            "Study from your materials with RAG, then take practice tests.\n"
            "Pick a category to begin:",
            reply_markup=categories_keyboard(),
        )
        await update.message.reply_text(
            "Menu shortcuts below (Study / Upload / Notes / Reminders).",
            reply_markup=main_reply_keyboard(),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            help_message(),
            reply_markup=main_reply_keyboard(),
            parse_mode="Markdown",
        )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("pong")
